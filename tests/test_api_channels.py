from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient


class _NoopWakeEvent:
    def set(self) -> None:
        return


def test_create_channel_json_and_list(client: TestClient) -> None:
    response = client.post(
        "/api/channels",
        json={
            "channel_id": "UCjson001",
            "channel_name": "JSON Channel",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["channel_id"] == "UCjson001"
    assert payload["channel_name"] == "JSON Channel"
    assert payload["is_active"] == 1

    list_response = client.get("/api/channels")
    assert list_response.status_code == 200
    channels = list_response.json()
    assert any(channel["channel_id"] == "UCjson001" for channel in channels)


def test_create_channel_form_and_list(client: TestClient) -> None:
    response = client.post(
        "/api/channels",
        data={
            "channel_id": "UCform001",
            "channel_name": "Form Channel",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["channel_id"] == "UCform001"
    assert payload["channel_name"] == "Form Channel"
    assert payload["is_active"] == 1

    list_response = client.get("/api/channels")
    assert list_response.status_code == 200
    channels = list_response.json()
    assert any(channel["channel_id"] == "UCform001" for channel in channels)


def test_update_channel_rss_priority(client: TestClient) -> None:
    response = client.post(
        "/api/channels",
        json={
            "channel_id": "UCpriority001",
            "channel_name": "Priority Channel",
        },
    )
    assert response.status_code == 200
    with sqlite3.connect(client.app.state.runtime.config.db_path) as conn:
        conn.execute(
            """
            UPDATE channels
            SET rss_next_poll_at = datetime('now', '+6 hours')
            WHERE channel_id = ?
            """,
            ("UCpriority001",),
        )
        conn.commit()

    update = client.patch(
        "/api/channels/UCpriority001/rss-priority",
        json={"priority": "pinned"},
    )

    assert update.status_code == 200
    assert update.json()["rss_priority"] == "pinned"
    next_poll_at = datetime.fromisoformat(update.json()["rss_next_poll_at"])
    assert abs((datetime.now(UTC) - next_poll_at.replace(tzinfo=UTC)).total_seconds()) < 5

    list_response = client.get("/api/channels")
    assert list_response.status_code == 200
    channels = list_response.json()
    assert any(
        channel["channel_id"] == "UCpriority001" and channel["rss_priority"] == "pinned"
        for channel in channels
    )


def test_update_channel_rss_priority_accepts_htmx_form_payload(client: TestClient) -> None:
    response = client.post(
        "/api/channels",
        json={
            "channel_id": "UCpriorityform001",
            "channel_name": "Priority Form Channel",
        },
    )
    assert response.status_code == 200

    update = client.patch(
        "/api/channels/UCpriorityform001/rss-priority",
        data={"priority": "low"},
        headers={"HX-Request": "true"},
    )

    assert update.status_code == 200
    assert update.json()["rss_priority"] == "low"


def test_update_channel_rss_priority_rejects_invalid_value(client: TestClient) -> None:
    response = client.post(
        "/api/channels",
        json={
            "channel_id": "UCprioritybad001",
            "channel_name": "Priority Bad Channel",
        },
    )
    assert response.status_code == 200

    update = client.patch(
        "/api/channels/UCprioritybad001/rss-priority",
        json={"priority": "urgent"},
    )

    assert update.status_code == 400
    assert update.json()["detail"] == "invalid rss priority"


def test_create_channel_json_accepts_metadata_fields(client: TestClient) -> None:
    client.app.state.runtime.channel_metadata_wake_event = _NoopWakeEvent()
    response = client.post(
        "/api/channels",
        json={
            "channel_id": "UCmEtA123456789012345678",
            "channel_name": "Meta Channel",
            "channel_handle": "@meta",
            "channel_url_canonical": "https://www.youtube.com/@meta",
            "channel_thumbnail_url": "https://i.ytimg.com/vi/meta/hqdefault.jpg",
            "channel_description": "channel description",
            "channel_language_hint": "ko",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["channel_id"] == "UCmEtA123456789012345678"
    assert payload["channel_handle"] == "@meta"
    assert payload["channel_url_canonical"] == "https://www.youtube.com/@meta"
    assert payload["channel_language_hint"] == "ko"
    assert payload["metadata_fetch_status"] == "pending"

    with sqlite3.connect(client.app.state.runtime.config.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                channel_handle,
                channel_url_canonical,
                channel_thumbnail_url,
                channel_description,
                channel_language_hint,
                metadata_fetch_status
            FROM channels
            WHERE channel_id = 'UCmEtA123456789012345678'
            """
        ).fetchone()
    assert row is not None
    assert str(row["channel_handle"]) == "@meta"
    assert str(row["channel_url_canonical"]) == "https://www.youtube.com/@meta"
    assert str(row["channel_thumbnail_url"]).startswith("https://i.ytimg.com/")
    assert str(row["channel_description"]) == "channel description"
    assert str(row["channel_language_hint"]) == "ko"
    assert str(row["metadata_fetch_status"]) == "pending"
