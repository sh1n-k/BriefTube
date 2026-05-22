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
                "UCretryui001",
                "Retry UI Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCretryui001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                video_id,
                "UCretryui001",
                "Retry UI Video",
                "2026-02-20T00:00:00+00:00",
                pipeline_status,
            ),
        )
        conn.commit()


def test_video_detail_shows_transcript_retry_button_for_failed_video(client: TestClient) -> None:
    video_id = "vid-retry-ui-001"
    _seed_video(video_id, "transcript_failed")

    detail = client.get(f"/videos/{video_id}")
    assert detail.status_code == 200
    assert "자막 재시도" in detail.text
    assert f'action="/videos/{video_id}/transcript/retry"' in detail.text

    submit = client.post(f"/videos/{video_id}/transcript/retry", follow_redirects=False)
    assert submit.status_code == 303
    assert submit.headers["location"] == f"/videos/{video_id}?transcript_retry=1"

    after = client.get(submit.headers["location"])
    assert after.status_code == 200
    assert "자막 재시도가 요청되었습니다." in after.text

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT pipeline_status, transcript_retry_count FROM videos WHERE video_id = ?",
            (video_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == "transcript_pending"
    assert row[1] == 0
