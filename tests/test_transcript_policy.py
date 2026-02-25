from __future__ import annotations

import asyncio
import os
import sqlite3

from app import repository
from app.database import open_database
from app.workers.transcript_worker import _compute_retry_delay_seconds


def test_compute_retry_delay_seconds_uses_exponential_backoff_with_cap() -> None:
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=0) == 120
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=1) == 240
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=4) == 1920
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=20) == 3600


def test_pop_pending_transcript_videos_prioritizes_recent_and_due(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCpolicy001", "Policy Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCpolicy001"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, transcript_status, transcript_next_attempt_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            ("vid-policy-old", "UCpolicy001", "old", "2026-01-01T00:00:00+00:00", None),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, transcript_status, transcript_next_attempt_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            ("vid-policy-new", "UCpolicy001", "new", "2026-02-01T00:00:00+00:00", "2000-01-01 00:00:00"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, transcript_status, transcript_next_attempt_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            ("vid-policy-future", "UCpolicy001", "future", "2026-03-01T00:00:00+00:00", "2999-01-01 00:00:00"),
        )
        conn.commit()

    async def _load_ids() -> list[str]:
        db = await open_database(db_path)
        try:
            rows = await repository.pop_pending_transcript_videos(db, limit=10)
            return [row["video_id"] for row in rows]
        finally:
            await db.close()

    ids = asyncio.run(_load_ids())
    assert ids == ["vid-policy-new", "vid-policy-old"]


def test_schedule_transcript_retry_updates_retry_count_and_next_attempt(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCretry001", "Retry Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCretry001"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, transcript_status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            ("vid-retry-001", "UCretry001", "retry", "2026-02-10T00:00:00+00:00"),
        )
        conn.commit()

    async def _run() -> tuple[int, str | None]:
        db = await open_database(db_path)
        try:
            await repository.schedule_transcript_retry(db, "vid-retry-001", delay_seconds=120)
            cursor = await db.execute(
                "SELECT transcript_retry_count, transcript_next_attempt_at FROM videos WHERE video_id = ?",
                ("vid-retry-001",),
            )
            row = await cursor.fetchone()
            return int(row["transcript_retry_count"]), row["transcript_next_attempt_at"]
        finally:
            await db.close()

    retry_count, next_attempt_at = asyncio.run(_run())
    assert retry_count == 1
    assert next_attempt_at is not None
