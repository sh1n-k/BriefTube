from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_channel_with_video(db_path: str, channel_id: str, video_id: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (channel_id, f"Name-{channel_id}", f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, transcript_status, restructure_status)
            VALUES (?, ?, ?, ?, 'done', 'done')
            """,
            (video_id, channel_id, f"Video-{video_id}", "2026-02-25T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            (video_id, "raw", "en", "manual"),
        )
        conn.execute(
            """
            INSERT INTO articles(video_id, title, lead, body, fact_box, timestamps)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (video_id, "title", "lead", "body", None, None),
        )
        conn.commit()


def test_delete_single_channel_removes_related_data(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel_with_video(db_path, "UCdel001", "vid-del-001")

    response = client.post("/views/channels/UCdel001/delete")
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT 1 FROM channels WHERE channel_id='UCdel001'").fetchone() is None
        assert conn.execute("SELECT 1 FROM videos WHERE video_id='vid-del-001'").fetchone() is None
        assert conn.execute("SELECT 1 FROM transcripts WHERE video_id='vid-del-001'").fetchone() is None
        assert conn.execute("SELECT 1 FROM articles WHERE video_id='vid-del-001'").fetchone() is None


def test_delete_selected_channels_removes_multiple(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel_with_video(db_path, "UCdel101", "vid-del-101")
    _seed_channel_with_video(db_path, "UCdel102", "vid-del-102")

    response = client.post(
        "/views/channels/delete-selected",
        data={"channel_id": ["UCdel101", "UCdel102"]},
    )
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        channel_count = conn.execute(
            "SELECT COUNT(1) FROM channels WHERE channel_id IN ('UCdel101', 'UCdel102')"
        ).fetchone()[0]
        video_count = conn.execute(
            "SELECT COUNT(1) FROM videos WHERE channel_id IN ('UCdel101', 'UCdel102')"
        ).fetchone()[0]
    assert channel_count == 0
    assert video_count == 0
