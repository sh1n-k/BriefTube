from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.services import bulk_channels
from app.services.bulk_channels import (
    collect_inputs_from_sources,
    parse_takeout_entries,
    resolve_bulk_inputs,
)


class _FakeResolver:
    def __init__(self, resolve_input) -> None:
        self.resolve_input = resolve_input


@pytest.mark.parametrize("bulk_text", ["resolved-input\nneeds-input\nfail-input"])
def test_bulk_resolve_with_mocked_resolver(bulk_text: str) -> None:
    async def fake_resolve_input(raw_input: str) -> dict:
        if raw_input == "resolved-input":
            return {
                "input": raw_input,
                "status": "resolved",
                "resolved": {
                    "channel_id": "UCresolved001",
                    "channel_name": "Resolved Channel",
                    "channel_url": "https://www.youtube.com/channel/UCresolved001",
                },
            }
        if raw_input == "needs-input":
            return {
                "input": raw_input,
                "status": "needs_selection",
                "candidates": [
                    {
                        "channel_id": "UCcand001",
                        "channel_name": "Candidate One",
                        "channel_url": "https://www.youtube.com/channel/UCcand001",
                    },
                    {
                        "channel_id": "UCcand002",
                        "channel_name": "Candidate Two",
                        "channel_url": "https://www.youtube.com/channel/UCcand002",
                    },
                ],
            }
        return {
            "input": raw_input,
            "status": "failed",
            "reason": "no match",
        }

    collected = collect_inputs_from_sources(
        bulk_text=bulk_text,
        takeout_data=parse_takeout_entries("takeout.txt", b""),
    )
    payload = asyncio.run(
        resolve_bulk_inputs(
            inputs=collected["inputs"],
            direct_channels=collected["direct_channels"],
            resolver=_FakeResolver(fake_resolve_input),
        )
    )

    assert payload["total_inputs"] == 3
    assert len(payload["resolved"]) == 1
    assert len(payload["needs_selection"]) == 1
    assert len(payload["failed"]) == 1


def test_bulk_commit_saves_unique_channels(client: TestClient) -> None:
    response = client.post(
        "/views/channels/bulk-commit",
        data={
            "resolved_channel_id": ["UCbulk001", "UCbulk001", "UCbulk002", ""],
            "resolved_channel_name": ["Bulk A", "Bulk A Duplicate", "Bulk B", "Invalid"],
        },
    )
    assert response.status_code == 200

    with sqlite3.connect(client.app.state.runtime.config.db_path) as conn:
        rows = conn.execute(
            "SELECT channel_id, channel_name FROM channels ORDER BY channel_id"
        ).fetchall()
    assert rows == [("UCbulk001", "Bulk A"), ("UCbulk002", "Bulk B")]


def test_bulk_resolve_google_csv_upload_directly_resolves(client: TestClient) -> None:
    csv_content = (
        "Channel Id,Channel Url,Channel Title\n"
        "UC0byV7SMA-MjzByM5fZR1EA,http://www.youtube.com/channel/UC0byV7SMA-MjzByM5fZR1EA,범죄심리 연구소\n"
    ).encode()

    response = client.post(
        "/views/channels/bulk-resolve",
        files={"takeout_file": ("subscriptions.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 200
    assert "Total: <b>1</b>" in response.text
    assert 'name="resolved_channel_id" value="UC0byV7SMA-MjzByM5fZR1EA"' in response.text


def test_bulk_resolve_rejects_oversized_takeout_upload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bulk_channels, "MAX_TAKEOUT_IMPORT_BYTES", 8)

    response = client.post(
        "/views/channels/bulk-resolve",
        files={"takeout_file": ("takeout.txt", b"https://www.youtube.com/@alpha", "text/plain")},
    )
    assert response.status_code == 413


def test_bulk_resolve_handles_resolver_exception() -> None:
    async def fake_resolve_input(raw_input: str) -> dict:
        if raw_input == "raise-input":
            raise RuntimeError("unexpected")
        return {
            "input": raw_input,
            "status": "resolved",
            "resolved": {
                "channel_id": "UCok001",
                "channel_name": "Resolved OK",
                "channel_url": "https://www.youtube.com/channel/UCok001",
            },
        }

    payload = asyncio.run(
        resolve_bulk_inputs(
            inputs=["raise-input", "safe-input"],
            resolver=_FakeResolver(fake_resolve_input),
        )
    )
    assert len(payload["resolved"]) == 1
    assert len(payload["failed"]) == 1
    assert payload["failed"][0]["input"] == "raise-input"
    assert payload["failed"][0]["reason"] == "resolver exception: RuntimeError"


def test_bulk_resolve_takeout_entries_uses_parser() -> None:
    collected = collect_inputs_from_sources(
        bulk_text="",
        takeout_data=parse_takeout_entries(
            "takeout.txt", b"hello https://www.youtube.com/@alpha world"
        ),
    )
    assert any("youtube.com/@alpha" in item for item in collected["inputs"])
