from __future__ import annotations

from pathlib import Path

import aiosqlite

from app.database_migrations import (
    _ensure_app_settings_table,
    _ensure_article_columns,
    _ensure_category_tables,
    _ensure_channel_metadata_columns,
    _ensure_download_columns,
    _ensure_manual_transcript_jobs_table,
    _ensure_remote_sync_device_id,
    _ensure_sync_metadata_columns,
    _ensure_video_columns,
    _ensure_video_indexes,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


async def open_database(db_path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path, timeout=5.0)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA synchronous = NORMAL;")
    await db.execute("PRAGMA busy_timeout = 5000;")
    return db


async def init_database(db: aiosqlite.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    await db.executescript(schema_sql)
    await _ensure_app_settings_table(db)
    await _ensure_video_columns(db)
    await _ensure_remote_sync_device_id(db)
    await _ensure_article_columns(db)
    await _ensure_video_indexes(db)
    await _ensure_download_columns(db)
    await _ensure_manual_transcript_jobs_table(db)
    await _ensure_category_tables(db)
    await _ensure_channel_metadata_columns(db)
    for table in ("categories", "channels", "videos", "transcripts", "articles"):
        await _ensure_sync_metadata_columns(db, table)
    await db.commit()


async def recover_stuck_jobs(db: aiosqlite.Connection) -> int:
    llm_cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending'
        WHERE pipeline_status = 'llm_processing'
        """
    )
    await db.commit()
    return int(llm_cursor.rowcount or 0)
