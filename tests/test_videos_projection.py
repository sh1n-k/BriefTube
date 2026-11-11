from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient

from app.repositories import videos as videos_repo
from tests.helpers.app_repo import call_repo
from tests.helpers.db_seed import seed_video_job_sync


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
                video_id, channel_id, title, upload_time, thumbnail_path
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "vid-proj-001",
                "UCproj001",
                "Projection Test Video",
                "2026-02-25T00:00:00+00:00",
                "./thumbnails-dev/vid-proj-001.jpg",
            ),
        )
        seed_video_job_sync(conn, "vid-proj-001", "transcript")
        conn.commit()

    rows = call_repo(
        client,
        videos_repo.list_videos,
        channel_id="UCproj001",
        sort="upload_time",
        order="desc",
        page=1,
        limit=20,
    )
    assert len(rows) == 1

    row = rows[0]
    assert row["channel_name"] == "Projection Channel"
    assert row["thumbnail_url"] == "/thumbnails/vid-proj-001.jpg"
