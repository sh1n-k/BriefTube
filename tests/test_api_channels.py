from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.repositories import channels as channels_repo
from tests.helpers.app_repo import call_repo


def test_update_channel_rss_priority(client: TestClient) -> None:
    call_repo(
        client,
        channels_repo.add_channel,
        channel_id="UCpriority001",
        channel_name="Priority Channel",
    )
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

    channels = call_repo(client, channels_repo.list_channels)
    assert any(
        channel["channel_id"] == "UCpriority001" and channel["rss_priority"] == "pinned"
        for channel in channels
    )


def test_update_channel_rss_priority_accepts_htmx_form_payload(client: TestClient) -> None:
    call_repo(
        client,
        channels_repo.add_channel,
        channel_id="UCpriorityform001",
        channel_name="Priority Form Channel",
    )

    update = client.patch(
        "/api/channels/UCpriorityform001/rss-priority",
        data={"priority": "low"},
        headers={"HX-Request": "true"},
    )

    assert update.status_code == 200
    assert update.json()["rss_priority"] == "low"


def test_update_channel_rss_priority_rejects_invalid_value(client: TestClient) -> None:
    call_repo(
        client,
        channels_repo.add_channel,
        channel_id="UCprioritybad001",
        channel_name="Priority Bad Channel",
    )

    update = client.patch(
        "/api/channels/UCprioritybad001/rss-priority",
        json={"priority": "urgent"},
    )

    assert update.status_code == 400
    assert update.json()["detail"] == "invalid rss priority"
