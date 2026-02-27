from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def test_videos_include_channel_name_and_thumbnail_url(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCproj001",
                "Projection Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCproj001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(
                video_id, channel_id, title, upload_time, thumbnail_path,
                pipeline_status, retry_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "vid-proj-001",
                "UCproj001",
                "Projection Test Video",
                "2026-02-25T00:00:00+00:00",
                "./thumbnails-dev/vid-proj-001.jpg",
                "transcript_pending",
                0,
            ),
        )
        conn.commit()

    response = client.get("/api/videos", params={"channel_id": "UCproj001"})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1

    row = rows[0]
    assert row["channel_name"] == "Projection Channel"
    assert row["thumbnail_url"] == "/thumbnails/vid-proj-001.jpg"
