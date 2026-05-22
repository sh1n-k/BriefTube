from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_single_video(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCtz001",
                "Timezone Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCtz001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(
                video_id, channel_id, title, upload_time, pipeline_status
            ) VALUES (?, ?, ?, ?, 'done')
            """,
            ("vid-tz-001", "UCtz001", "Timezone Video", "2026-02-25T00:00:00+00:00"),
        )
        conn.commit()


def test_home_upload_time_uses_default_seoul_timezone(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_single_video(db_path)

    response = client.get("/")
    assert response.status_code == 200
    assert "2026-02-25 09:00" in response.text


def test_home_upload_time_changes_with_timezone_setting(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_single_video(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES('timezone', 'America/New_York')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
        )
        conn.commit()

    response = client.get("/")
    assert response.status_code == 200
    assert "2026-02-24 19:00" in response.text
