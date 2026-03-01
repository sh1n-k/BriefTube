from __future__ import annotations

import logging
from pathlib import Path
import aiosqlite


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
VALID_PIPELINE_STATUSES: tuple[str, ...] = (
    "transcript_pending",
    "transcript_processing",
    "transcript_failed",
    "no_subtitle",
    "llm_pending",
    "llm_processing",
    "llm_failed",
    "manual_review",
    "done",
)
logger = logging.getLogger(__name__)


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
    await _ensure_download_columns(db)
    await db.commit()


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return any(row["name"] == column for row in rows)


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {str(row["name"]) for row in rows}


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
    columns = await _table_columns(db, "videos")
    needs_rebuild = (
        "pipeline_status" not in columns
        or "transcript_status" in columns
        or "restructure_status" in columns
    )
    if needs_rebuild:
        await _rebuild_videos_table_with_pipeline_status(db, columns)
        columns = await _table_columns(db, "videos")

    if "transcript_retry_count" not in columns:
        await db.execute("ALTER TABLE videos ADD COLUMN transcript_retry_count INTEGER NOT NULL DEFAULT 0")
    if "transcript_next_attempt_at" not in columns:
        await db.execute("ALTER TABLE videos ADD COLUMN transcript_next_attempt_at TEXT")
    if "transcript_target_language" not in columns:
        await db.execute("ALTER TABLE videos ADD COLUMN transcript_target_language TEXT")
    if "transcript_last_error" not in columns:
        await db.execute("ALTER TABLE videos ADD COLUMN transcript_last_error TEXT")
    if "transcript_last_error_at" not in columns:
        await db.execute("ALTER TABLE videos ADD COLUMN transcript_last_error_at TEXT")
    if "retry_count" not in columns:
        await db.execute("ALTER TABLE videos ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
    if "created_at" not in columns:
        await db.execute("ALTER TABLE videos ADD COLUMN created_at TEXT")
        await db.execute("UPDATE videos SET created_at = COALESCE(created_at, datetime('now'))")

    if "viewed_at" not in columns:
        await db.execute("ALTER TABLE videos ADD COLUMN viewed_at TEXT")

    normalized = await _normalize_pipeline_status_values(db)
    if normalized > 0:
        logger.warning(
            "event=db.pipeline_status_normalized normalized=%s",
            normalized,
            extra={"event": "db.pipeline_status_normalized"},
        )


def _source_column_expr(columns: set[str], column: str, fallback_sql: str) -> str:
    return column if column in columns else fallback_sql


def _pipeline_status_expr(columns: set[str]) -> str:
    if "pipeline_status" in columns:
        return """
            CASE lower(trim(coalesce(pipeline_status, '')))
                WHEN 'transcript_pending' THEN 'transcript_pending'
                WHEN 'transcript_processing' THEN 'transcript_processing'
                WHEN 'transcript_failed' THEN 'transcript_failed'
                WHEN 'no_subtitle' THEN 'no_subtitle'
                WHEN 'llm_pending' THEN 'llm_pending'
                WHEN 'llm_processing' THEN 'llm_processing'
                WHEN 'llm_failed' THEN 'llm_failed'
                WHEN 'manual_review' THEN 'manual_review'
                WHEN 'done' THEN 'done'
                WHEN 'pending' THEN 'transcript_pending'
                WHEN 'processing' THEN 'llm_processing'
                WHEN 'failed' THEN 'llm_failed'
                ELSE 'manual_review'
            END
        """.strip()
    transcript_expr = _source_column_expr(columns, "transcript_status", "''")
    restructure_expr = _source_column_expr(columns, "restructure_status", "''")
    return f"""
        CASE
            WHEN {restructure_expr} = 'done' THEN 'done'
            WHEN {restructure_expr} = 'processing' THEN 'llm_processing'
            WHEN {restructure_expr} = 'manual_review' THEN 'manual_review'
            WHEN {restructure_expr} = 'failed' THEN 'llm_failed'
            WHEN {transcript_expr} = 'done' THEN 'llm_pending'
            WHEN {transcript_expr} = 'no_subtitle' THEN 'no_subtitle'
            WHEN {transcript_expr} = 'failed' THEN 'transcript_failed'
            ELSE 'transcript_pending'
        END
    """.strip()


async def _normalize_pipeline_status_values(db: aiosqlite.Connection) -> int:
    canonical = ",".join([f"'{value}'" for value in VALID_PIPELINE_STATUSES])
    cursor = await db.execute(
        f"""
        UPDATE videos
        SET pipeline_status = CASE lower(trim(coalesce(pipeline_status, '')))
            WHEN 'pending' THEN 'transcript_pending'
            WHEN 'processing' THEN 'llm_processing'
            WHEN 'failed' THEN 'llm_failed'
            ELSE 'manual_review'
        END
        WHERE lower(trim(coalesce(pipeline_status, ''))) NOT IN ({canonical})
        """
    )
    return int(cursor.rowcount or 0)


async def _rebuild_videos_table_with_pipeline_status(
    db: aiosqlite.Connection,
    columns: set[str],
) -> None:
    pipeline_expr = _pipeline_status_expr(columns)
    transcript_retry_expr = _source_column_expr(columns, "transcript_retry_count", "0")
    transcript_next_expr = _source_column_expr(columns, "transcript_next_attempt_at", "NULL")
    transcript_lang_expr = _source_column_expr(columns, "transcript_target_language", "NULL")
    transcript_error_expr = _source_column_expr(columns, "transcript_last_error", "NULL")
    transcript_error_at_expr = _source_column_expr(columns, "transcript_last_error_at", "NULL")
    retry_count_expr = _source_column_expr(columns, "retry_count", "0")
    created_at_expr = _source_column_expr(columns, "created_at", "datetime('now')")
    viewed_at_expr = _source_column_expr(columns, "viewed_at", "NULL")

    pragma_cursor = await db.execute("PRAGMA foreign_keys")
    pragma_row = await pragma_cursor.fetchone()
    foreign_keys_enabled = bool(int(pragma_row[0] if pragma_row is not None else 1))
    before_cursor = await db.execute("SELECT COUNT(1) AS cnt FROM videos")
    before_row = await before_cursor.fetchone()
    source_count = int(before_row["cnt"] if before_row is not None else 0)

    if foreign_keys_enabled:
        await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute(
            """
            CREATE TABLE videos_new (
                video_id                TEXT PRIMARY KEY,
                channel_id              TEXT NOT NULL REFERENCES channels(channel_id),
                title                   TEXT NOT NULL,
                upload_time             TEXT NOT NULL,
                thumbnail_path          TEXT,
                pipeline_status         TEXT NOT NULL DEFAULT 'transcript_pending',
                transcript_retry_count  INTEGER NOT NULL DEFAULT 0,
                transcript_next_attempt_at TEXT,
                transcript_target_language TEXT,
                transcript_last_error   TEXT,
                transcript_last_error_at TEXT,
                retry_count             INTEGER NOT NULL DEFAULT 0,
                created_at              TEXT NOT NULL DEFAULT (datetime('now')),
                viewed_at               TEXT
            )
            """
        )
        await db.execute(
            f"""
            INSERT INTO videos_new (
                video_id,
                channel_id,
                title,
                upload_time,
                thumbnail_path,
                pipeline_status,
                transcript_retry_count,
                transcript_next_attempt_at,
                transcript_target_language,
                transcript_last_error,
                transcript_last_error_at,
                retry_count,
                created_at,
                viewed_at
            )
            SELECT
                video_id,
                channel_id,
                title,
                upload_time,
                thumbnail_path,
                {pipeline_expr},
                {transcript_retry_expr},
                {transcript_next_expr},
                {transcript_lang_expr},
                {transcript_error_expr},
                {transcript_error_at_expr},
                {retry_count_expr},
                {created_at_expr},
                {viewed_at_expr}
            FROM videos
            """
        )
        await db.execute("DROP TABLE videos")
        await db.execute("ALTER TABLE videos_new RENAME TO videos")
        after_cursor = await db.execute("SELECT COUNT(1) AS cnt FROM videos")
        after_row = await after_cursor.fetchone()
        target_count = int(after_row["cnt"] if after_row is not None else 0)
        await db.commit()
        logger.info(
            "event=db.videos_rebuild_completed source_count=%s target_count=%s",
            source_count,
            target_count,
            extra={"event": "db.videos_rebuild_completed"},
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "event=db.videos_rebuild_failed source_count=%s",
            source_count,
            extra={"event": "db.videos_rebuild_failed"},
        )
        raise
    finally:
        if foreign_keys_enabled:
            await db.execute("PRAGMA foreign_keys = ON")


async def _ensure_video_indexes(db: aiosqlite.Connection) -> None:
    await db.execute("DROP INDEX IF EXISTS idx_videos_restructure")
    await db.execute("DROP INDEX IF EXISTS idx_videos_transcript_queue")
    await db.execute("DROP INDEX IF EXISTS idx_videos_pipeline")
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_videos_transcript_queue
        ON videos(pipeline_status, transcript_next_attempt_at, upload_time DESC)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_videos_pipeline
        ON videos(pipeline_status)
        """
    )


async def _ensure_download_columns(db: aiosqlite.Connection) -> None:
    if not await _column_exists(db, "download_jobs", "target_dir"):
        await db.execute("ALTER TABLE download_jobs ADD COLUMN target_dir TEXT")


async def recover_stuck_jobs(db: aiosqlite.Connection) -> int:
    llm_cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending'
        WHERE pipeline_status = 'llm_processing'
        """
    )
    transcript_cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_pending'
        WHERE pipeline_status = 'transcript_processing'
        """
    )
    await db.commit()
    return int(llm_cursor.rowcount or 0) + int(transcript_cursor.rowcount or 0)
