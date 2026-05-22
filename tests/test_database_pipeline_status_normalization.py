from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.database import init_database, open_database


def test_init_database_normalizes_legacy_pipeline_status_values(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_pipeline.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                pipeline_status TEXT,
                transcript_next_attempt_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCnorm001",
                "Normalize Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCnorm001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES
                (?, ?, ?, ?, ?),
                (?, ?, ?, ?, ?),
                (?, ?, ?, ?, ?),
                (?, ?, ?, ?, ?),
                (?, ?, ?, ?, ?)
            """,
            (
                "vid-norm-pending",
                "UCnorm001",
                "legacy pending",
                "2026-02-20T00:00:00+00:00",
                "pending",
                "vid-norm-processing",
                "UCnorm001",
                "legacy processing",
                "2026-02-20T00:00:00+00:00",
                "processing",
                "vid-norm-failed",
                "UCnorm001",
                "legacy failed",
                "2026-02-20T00:00:00+00:00",
                "failed",
                "vid-norm-unknown",
                "UCnorm001",
                "legacy unknown",
                "2026-02-20T00:00:00+00:00",
                "mystery",
                "vid-norm-done",
                "UCnorm001",
                "legacy done",
                "2026-02-20T00:00:00+00:00",
                "done",
            ),
        )
        conn.commit()

    async def _run() -> dict[str, str]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            cursor = await db.execute(
                """
                SELECT video_id, pipeline_status
                FROM videos
                ORDER BY video_id ASC
                """
            )
            rows = await cursor.fetchall()
            return {str(row["video_id"]): str(row["pipeline_status"]) for row in rows}
        finally:
            await db.close()

    statuses = asyncio.run(_run())
    assert statuses["vid-norm-pending"] == "transcript_pending"
    assert statuses["vid-norm-processing"] == "llm_processing"
    assert statuses["vid-norm-failed"] == "llm_failed"
    assert statuses["vid-norm-unknown"] == "manual_review"
    assert statuses["vid-norm-done"] == "done"


def test_init_database_migrates_legacy_split_status_columns_before_index_creation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy_split_status.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                thumbnail_path TEXT,
                transcript_status TEXT NOT NULL DEFAULT 'pending',
                restructure_status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UClegacy001",
                "Legacy Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UClegacy001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(
                video_id, channel_id, title, upload_time, thumbnail_path, transcript_status, restructure_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "vid-legacy-001",
                "UClegacy001",
                "legacy split status",
                "2026-02-20T00:00:00+00:00",
                None,
                "done",
                "pending",
            ),
        )
        conn.commit()

    async def _run() -> tuple[str, bool]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            status_cursor = await db.execute(
                "SELECT pipeline_status FROM videos WHERE video_id = ?",
                ("vid-legacy-001",),
            )
            status_row = await status_cursor.fetchone()
            index_cursor = await db.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'index' AND name = 'idx_videos_transcript_queue'
                """
            )
            return str(status_row["pipeline_status"]), (await index_cursor.fetchone()) is not None
        finally:
            await db.close()

    status, has_queue_index = asyncio.run(_run())
    assert status == "llm_pending"
    assert has_queue_index is True
