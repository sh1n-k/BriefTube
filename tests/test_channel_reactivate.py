from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_channel(
    db_path: str,
    channel_id: str,
    channel_name: str,
    *,
    is_active: int,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, ?)
            """,
            (
                channel_id,
                channel_name,
                f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                is_active,
            ),
        )
        conn.commit()


def _seed_rss_not_found_alert(
    db_path: str,
    channel_id: str,
    channel_name: str,
    message: str,
    created_at: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO system_alerts(alert_type, channel_id, channel_name, message, created_at)
            VALUES ('rss_channel_not_found', ?, ?, ?, ?)
            """,
            (channel_id, channel_name, message, created_at),
        )
        conn.commit()


def test_inactive_tab_renders_reason_and_time(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive001", "Inactive One", is_active=0)
    _seed_rss_not_found_alert(
        db_path,
        "UCinactive001",
        "Inactive One",
        "RSS returned 404",
        "2026-02-27 12:34:56",
    )

    response = client.get("/channels?status=inactive")
    assert response.status_code == 200
    assert "비활성 사유: RSS returned 404" in response.text
    assert "비활성 시각: 2026-02-27 12:34:56" in response.text
    assert "/views/channels/UCinactive001/reactivate?status=inactive" in response.text


def test_inactive_tab_renders_unknown_when_alert_missing(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive002", "Inactive Two", is_active=0)

    response = client.get("/views/channel-list?status=inactive")
    assert response.status_code == 200
    assert response.text.count("알 수 없음") >= 1


def test_reactivate_single_channel(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive101", "Inactive 101", is_active=0)

    response = client.post("/views/channels/UCinactive101/reactivate?status=inactive")
    assert response.status_code == 200
    assert "UCinactive101" not in response.text

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT is_active FROM channels WHERE channel_id='UCinactive101'"
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 1


def test_reactivate_selected_channels(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive201", "Inactive 201", is_active=0)
    _seed_channel(db_path, "UCinactive202", "Inactive 202", is_active=0)

    response = client.post(
        "/views/channels/reactivate-selected",
        data={"status": "inactive", "channel_id": ["UCinactive201", "UCinactive202"]},
    )
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        active_count = conn.execute(
            """
            SELECT COUNT(1)
            FROM channels
            WHERE channel_id IN ('UCinactive201', 'UCinactive202') AND is_active = 1
            """
        ).fetchone()[0]
    assert int(active_count) == 2


def test_reactivate_selected_delete_action_removes_channels(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive301", "Inactive 301", is_active=0)
    _seed_channel(db_path, "UCinactive302", "Inactive 302", is_active=0)

    response = client.post(
        "/views/channels/reactivate-selected",
        data={
            "status": "inactive",
            "bulk_action": "delete",
            "channel_id": ["UCinactive301", "UCinactive302"],
        },
    )
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        remain = conn.execute(
            """
            SELECT COUNT(1)
            FROM channels
            WHERE channel_id IN ('UCinactive301', 'UCinactive302')
            """
        ).fetchone()[0]
    assert int(remain) == 0
