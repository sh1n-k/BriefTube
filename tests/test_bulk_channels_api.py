from __future__ import annotations

from fastapi.testclient import TestClient
import pytest


@pytest.mark.parametrize("bulk_text", ["resolved-input\nneeds-input\nfail-input"])
def test_bulk_resolve_with_mocked_resolver(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    bulk_text: str,
) -> None:
    resolver = client.app.state.runtime.channel_resolver

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

    monkeypatch.setattr(resolver, "resolve_input", fake_resolve_input)

    response = client.post(
        "/api/channels/bulk/resolve",
        json={"bulk_text": bulk_text},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["total_inputs"] == 3
    assert len(payload["resolved"]) == 1
    assert len(payload["needs_selection"]) == 1
    assert len(payload["failed"]) == 1


def test_bulk_commit_saves_unique_channels(client: TestClient) -> None:
    response = client.post(
        "/api/channels/bulk/commit",
        json={
            "items": [
                {"channel_id": "UCbulk001", "channel_name": "Bulk A"},
                {"channel_id": "UCbulk001", "channel_name": "Bulk A Duplicate"},
                {"channel_id": "UCbulk002", "channel_name": "Bulk B"},
                {"channel_id": "", "channel_name": "Invalid"},
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["saved"] == 2

    channels = client.get("/api/channels").json()
    ids = {item["channel_id"] for item in channels}
    assert "UCbulk001" in ids
    assert "UCbulk002" in ids


def test_bulk_resolve_google_csv_upload_directly_resolves(client: TestClient) -> None:
    csv_content = (
        "Channel Id,Channel Url,Channel Title\n"
        "UC0byV7SMA-MjzByM5fZR1EA,http://www.youtube.com/channel/UC0byV7SMA-MjzByM5fZR1EA,범죄심리 연구소\n"
    ).encode("utf-8")

    response = client.post(
        "/api/channels/bulk/resolve",
        files={"takeout_file": ("subscriptions.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["total_inputs"] == 1
    assert len(payload["resolved"]) == 1
    assert payload["resolved"][0]["resolved"]["channel_id"] == "UC0byV7SMA-MjzByM5fZR1EA"
    assert len(payload["needs_selection"]) == 0
