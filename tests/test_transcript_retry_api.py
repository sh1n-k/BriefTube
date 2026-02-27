from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_video(video_id: str, pipeline_status: str) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCretryapi001",
                "Retry API Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCretryapi001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(
                video_id,
                channel_id,
                title,
                upload_time,
                pipeline_status,
                transcript_retry_count,
                transcript_next_attempt_at,
                transcript_last_error,
                transcript_last_error_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                "UCretryapi001",
                "Retry API Video",
                "2026-02-20T00:00:00+00:00",
                pipeline_status,
                3,
                "2099-01-01 00:00:00",
                "old error",
                "2026-02-20T00:00:00+00:00",
            ),
        )
        conn.commit()


def test_retry_transcript_api_resets_failed_video(client: TestClient) -> None:
    _seed_video("vid-retry-api-001", "transcript_failed")

    response = client.post("/api/videos/vid-retry-api-001/transcript/retry")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "video_id": "vid-retry-api-001"}

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT pipeline_status, transcript_retry_count, transcript_next_attempt_at,
                   transcript_last_error, transcript_last_error_at
            FROM videos WHERE video_id = ?
            """,
            ("vid-retry-api-001",),
        ).fetchone()

    assert row is not None
    assert row[0] == "transcript_pending"
    assert row[1] == 0
    assert row[2] is None
    assert row[3] is None
    assert row[4] is None


def test_retry_transcript_api_rejects_non_retryable_status(client: TestClient) -> None:
    _seed_video("vid-retry-api-002", "done")

    response = client.post("/api/videos/vid-retry-api-002/transcript/retry")
    assert response.status_code == 404
