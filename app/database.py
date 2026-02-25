from __future__ import annotations

from pathlib import Path
import aiosqlite


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


async def open_database(db_path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA synchronous = NORMAL;")
    return db


async def init_database(db: aiosqlite.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    await db.executescript(schema_sql)
    await _ensure_app_settings_table(db)
    await _ensure_video_columns(db)
    await _ensure_video_indexes(db)
    await db.commit()


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return any(row["name"] == column for row in rows)


async def _ensure_app_settings_table(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


async def _ensure_video_columns(db: aiosqlite.Connection) -> None:
    if not await _column_exists(db, "videos", "transcript_retry_count"):
        await db.execute(
            "ALTER TABLE videos ADD COLUMN transcript_retry_count INTEGER NOT NULL DEFAULT 0"
        )
    if not await _column_exists(db, "videos", "transcript_next_attempt_at"):
        await db.execute("ALTER TABLE videos ADD COLUMN transcript_next_attempt_at TEXT")


async def _ensure_video_indexes(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_videos_transcript_queue
        ON videos(transcript_status, transcript_next_attempt_at, upload_time DESC)
        """
    )


async def recover_stuck_jobs(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET restructure_status = 'pending'
        WHERE restructure_status = 'processing'
        """
    )
    await db.commit()
    return cursor.rowcount
