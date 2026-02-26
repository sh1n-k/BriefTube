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


def test_schedule_transcript_retry_persists_last_error(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCretry002", "Retry Channel 2", "https://www.youtube.com/feeds/videos.xml?channel_id=UCretry002"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, transcript_status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            ("vid-retry-002", "UCretry002", "retry2", "2026-02-11T00:00:00+00:00"),
        )
        conn.commit()

    async def _run() -> tuple[int, str | None]:
        db = await open_database(db_path)
        try:
            await repository.schedule_transcript_retry(
                db,
                "vid-retry-002",
                delay_seconds=120,
                error_message="network timeout",
            )
            cursor = await db.execute(
                "SELECT transcript_retry_count, transcript_last_error FROM videos WHERE video_id = ?",
                ("vid-retry-002",),
            )
            row = await cursor.fetchone()
            return int(row["transcript_retry_count"]), row["transcript_last_error"]
        finally:
            await db.close()

    retry_count, last_error = asyncio.run(_run())
    assert retry_count == 1
    assert last_error == "network timeout"


def test_save_transcript_sets_target_language_and_clears_last_error(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCsave001", "Save Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCsave001"),
        )
        conn.execute(
            """
            INSERT INTO videos(
                video_id,
                channel_id,
                title,
                upload_time,
                transcript_status,
                transcript_last_error,
                transcript_last_error_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                "vid-save-001",
                "UCsave001",
                "save",
                "2026-02-12T00:00:00+00:00",
                "old error",
                "2026-02-12T00:00:00+00:00",
            ),
        )
        conn.commit()

    async def _run() -> tuple[str, str | None, str | None, str | None]:
        db = await open_database(db_path)
        try:
            await repository.save_transcript(
                db,
                video_id="vid-save-001",
                raw_text="hello",
                language="ko",
                source_type="manual",
                thumbnail_path=None,
            )
            cursor = await db.execute(
                """
                SELECT transcript_status, transcript_target_language, transcript_last_error, transcript_last_error_at
                FROM videos
                WHERE video_id = ?
                """,
                ("vid-save-001",),
            )
            row = await cursor.fetchone()
            return (
                str(row["transcript_status"]),
                row["transcript_target_language"],
                row["transcript_last_error"],
                row["transcript_last_error_at"],
            )
        finally:
            await db.close()

    status, target_language, last_error, last_error_at = asyncio.run(_run())
    assert status == "done"
    assert target_language == "ko"
    assert last_error is None
    assert last_error_at is None


def test_pop_pending_transcript_videos_can_avoid_last_channel(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1), (?, ?, ?, 1)
            """,
            (
                "UCfair001",
                "Fair Channel A",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCfair001",
                "UCfair002",
                "Fair Channel B",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCfair002",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, transcript_status)
            VALUES
              (?, ?, ?, ?, 'pending'),
              (?, ?, ?, ?, 'pending')
            """,
            (
                "vid-fair-a",
                "UCfair001",
                "newest",
                "2026-02-28T00:00:00+00:00",
                "vid-fair-b",
                "UCfair002",
                "older",
                "2026-02-27T00:00:00+00:00",
            ),
        )
        conn.commit()

    async def _run() -> tuple[str, str]:
        db = await open_database(db_path)
        try:
            normal = await repository.pop_pending_transcript_videos(db, limit=1, lookahead=10)
            avoided = await repository.pop_pending_transcript_videos(
                db,
                limit=1,
                lookahead=10,
                avoid_channel_id="UCfair001",
            )
            return normal[0]["video_id"], avoided[0]["video_id"]
        finally:
            await db.close()

    normal_id, avoided_id = asyncio.run(_run())
    assert normal_id == "vid-fair-a"
    assert avoided_id == "vid-fair-b"


def test_defer_channel_transcript_retries_defers_same_channel_except_excluded(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCdefer001", "Defer Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCdefer001"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, transcript_status)
            VALUES
              (?, ?, ?, ?, 'pending'),
              (?, ?, ?, ?, 'pending')
            """,
            (
                "vid-defer-keep",
                "UCdefer001",
                "keep",
                "2026-02-25T00:00:00+00:00",
                "vid-defer-move",
                "UCdefer001",
                "move",
                "2026-02-24T00:00:00+00:00",
            ),
        )
        conn.commit()

    async def _run() -> tuple[str | None, str | None]:
        db = await open_database(db_path)
        try:
            await repository.defer_channel_transcript_retries(
                db,
                channel_id="UCdefer001",
                delay_seconds=600,
                exclude_video_id="vid-defer-keep",
            )
            cursor = await db.execute(
                """
                SELECT video_id, transcript_next_attempt_at
                FROM videos
                WHERE video_id IN ('vid-defer-keep', 'vid-defer-move')
                ORDER BY video_id ASC
                """
            )
            rows = await cursor.fetchall()
            return rows[0]["transcript_next_attempt_at"], rows[1]["transcript_next_attempt_at"]
        finally:
            await db.close()

    keep_next_attempt_at, move_next_attempt_at = asyncio.run(_run())
    assert keep_next_attempt_at is None
    assert move_next_attempt_at is not None


def test_transcript_worker_lease_allows_single_owner(client) -> None:
    db_path = os.environ["DB_PATH"]

    async def _run() -> tuple[bool, bool, bool, bool, bool, bool]:
        db = await open_database(db_path)
        try:
            acquired_a = await repository.acquire_transcript_worker_lease(
                db,
                owner_id="owner-a",
                ttl_seconds=60,
            )
            acquired_b = await repository.acquire_transcript_worker_lease(
                db,
                owner_id="owner-b",
                ttl_seconds=60,
            )
            renewed_a = await repository.renew_transcript_worker_lease(
                db,
                owner_id="owner-a",
                ttl_seconds=60,
            )
            released_b = await repository.release_transcript_worker_lease(db, owner_id="owner-b")
            released_a = await repository.release_transcript_worker_lease(db, owner_id="owner-a")
            acquired_b_after_release = await repository.acquire_transcript_worker_lease(
                db,
                owner_id="owner-b",
                ttl_seconds=60,
            )
            return acquired_a, acquired_b, renewed_a, released_b, released_a, acquired_b_after_release
        finally:
            await db.close()

    acquired_a, acquired_b, renewed_a, released_b, released_a, acquired_b_after_release = asyncio.run(_run())
    assert acquired_a is True
    assert acquired_b is False
    assert renewed_a is True
    assert released_b is False
    assert released_a is True
    assert acquired_b_after_release is True
