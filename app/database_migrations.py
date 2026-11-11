"""Schema migration helpers for the SQLite database.

Extracted verbatim from app.database to keep the public DB surface thin.
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)
CURRENT_SCHEMA_VERSION = 4
# videos.pipeline_status values from schema v1-v3. Only the legacy migrations use them.
_LEGACY_PIPELINE_STATUSES: tuple[str, ...] = (
    "auto_paused",
    "transcript_pending",
    "transcript_processing",
    "transcript_done",
    "transcript_failed",
    "no_subtitle",
    "llm_pending",
    "llm_processing",
    "llm_failed",
    "manual_review",
    "done",
)
DEFAULT_CATEGORY_UID = "default"
UPDATED_AT_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return any(row["name"] == column for row in rows)


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {str(row["name"]) for row in rows}


def _source_column_expr(columns: set[str], column: str, fallback_sql: str) -> str:
    return column if column in columns else fallback_sql


def _pipeline_status_expr(columns: set[str]) -> str:
    if "pipeline_status" in columns:
        return """
            CASE lower(trim(coalesce(pipeline_status, '')))
                WHEN 'transcript_pending' THEN 'transcript_pending'
                WHEN 'transcript_processing' THEN 'transcript_processing'
                WHEN 'transcript_done' THEN 'transcript_done'
                WHEN 'transcript_failed' THEN 'transcript_failed'
                WHEN 'no_subtitle' THEN 'no_subtitle'
                WHEN 'auto_paused' THEN 'auto_paused'
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
    canonical = ",".join([f"'{value}'" for value in _LEGACY_PIPELINE_STATUSES])
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


async def _normalize_processing_stage_snapshot_values(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET processing_stage_snapshot = CASE lower(trim(coalesce(processing_stage_snapshot, '')))
            WHEN 'off' THEN 'off'
            WHEN 'transcript_only' THEN 'transcript_only'
            WHEN 'full' THEN 'full'
            ELSE 'full'
        END
        WHERE lower(trim(coalesce(processing_stage_snapshot, ''))) NOT IN ('off', 'transcript_only', 'full')
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
    updated_at_expr = _source_column_expr(
        columns,
        "updated_at",
        f"COALESCE({created_at_expr}, {UPDATED_AT_SQL})",
    )
    processing_stage_snapshot_expr = _source_column_expr(
        columns, "processing_stage_snapshot", "'full'"
    )
    live_filter = "WHERE deleted_at IS NULL" if "deleted_at" in columns else ""

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
                processing_stage_snapshot TEXT NOT NULL DEFAULT 'full',
                transcript_retry_count  INTEGER NOT NULL DEFAULT 0,
                transcript_next_attempt_at TEXT,
                transcript_target_language TEXT,
                transcript_last_error   TEXT,
                transcript_last_error_at TEXT,
                retry_count             INTEGER NOT NULL DEFAULT 0,
                created_at              TEXT NOT NULL DEFAULT (datetime('now')),
                viewed_at               TEXT,
                updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
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
                processing_stage_snapshot,
                transcript_retry_count,
                transcript_next_attempt_at,
                transcript_target_language,
                transcript_last_error,
                transcript_last_error_at,
                retry_count,
                created_at,
                viewed_at,
                updated_at
            )
            SELECT
                video_id,
                channel_id,
                title,
                upload_time,
                thumbnail_path,
                {pipeline_expr},
                CASE lower(trim(coalesce({processing_stage_snapshot_expr}, '')))
                    WHEN 'off' THEN 'off'
                    WHEN 'transcript_only' THEN 'transcript_only'
                    WHEN 'full' THEN 'full'
                    ELSE 'full'
                END,
                {transcript_retry_expr},
                {transcript_next_expr},
                {transcript_lang_expr},
                {transcript_error_expr},
                {transcript_error_at_expr},
                {retry_count_expr},
                {created_at_expr},
                {viewed_at_expr},
                COALESCE(NULLIF(trim({updated_at_expr}), ''), {created_at_expr}, {UPDATED_AT_SQL})
            FROM videos
            {live_filter}
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


async def _ensure_updated_at_column(
    db: aiosqlite.Connection,
    table: str,
) -> None:
    columns = await _table_columns(db, table)
    if "updated_at" not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")
    await db.execute(
        f"""
        UPDATE {table}
        SET updated_at = COALESCE(NULLIF(trim(updated_at), ''), created_at, {UPDATED_AT_SQL})
        WHERE updated_at IS NULL OR trim(updated_at) = ''
        """
    )


async def _ensure_video_columns(db: aiosqlite.Connection) -> None:
    columns = await _table_columns(db, "videos")
    if "target_stage" in columns:
        # Created from the current schema.sql; the v1 pipeline_status shape does not apply.
        return
    needs_rebuild = (
        "pipeline_status" not in columns
        or "transcript_status" in columns
        or "restructure_status" in columns
    )
    if needs_rebuild:
        await _rebuild_videos_table_with_pipeline_status(db, columns)
        columns = await _table_columns(db, "videos")

    if "transcript_retry_count" not in columns:
        await db.execute(
            "ALTER TABLE videos ADD COLUMN transcript_retry_count INTEGER NOT NULL DEFAULT 0"
        )
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

    added_processing_stage_snapshot = False
    if "processing_stage_snapshot" not in columns:
        await db.execute(
            "ALTER TABLE videos ADD COLUMN processing_stage_snapshot TEXT NOT NULL DEFAULT 'full'"
        )
        added_processing_stage_snapshot = True

    if added_processing_stage_snapshot:
        has_channel_category = await _column_exists(db, "channels", "category_id")
        has_category_stage = await _column_exists(db, "categories", "processing_stage")
        if has_channel_category and has_category_stage:
            await db.execute(
                """
                UPDATE videos
                SET processing_stage_snapshot = COALESCE(
                    (
                        SELECT CASE lower(trim(coalesce(cat.processing_stage, '')))
                            WHEN 'off' THEN 'off'
                            WHEN 'transcript_only' THEN 'transcript_only'
                            WHEN 'full' THEN 'full'
                            ELSE 'full'
                        END
                        FROM channels ch
                        LEFT JOIN categories cat ON cat.id = ch.category_id
                        WHERE ch.channel_id = videos.channel_id
                    ),
                    'full'
                )
                """
            )

    normalized_stage = await _normalize_processing_stage_snapshot_values(db)
    if normalized_stage > 0:
        logger.warning(
            "event=db.processing_stage_snapshot_normalized normalized=%s",
            normalized_stage,
            extra={"event": "db.processing_stage_snapshot_normalized"},
        )

    normalized = await _normalize_pipeline_status_values(db)
    if normalized > 0:
        logger.warning(
            "event=db.pipeline_status_normalized normalized=%s",
            normalized,
            extra={"event": "db.pipeline_status_normalized"},
        )


async def _ensure_video_indexes(db: aiosqlite.Connection) -> None:
    await db.execute("DROP INDEX IF EXISTS idx_videos_restructure")
    await db.execute("DROP INDEX IF EXISTS idx_videos_transcript_queue")
    await db.execute("DROP INDEX IF EXISTS idx_videos_pipeline")
    if not await _column_exists(db, "videos", "pipeline_status"):
        return
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


async def _ensure_article_columns(db: aiosqlite.Connection) -> None:
    migrated = False
    if not await _column_exists(db, "articles", "llm_provider"):
        await db.execute(
            "ALTER TABLE articles ADD COLUMN llm_provider TEXT NOT NULL DEFAULT 'unknown'"
        )
        migrated = True
    if not await _column_exists(db, "articles", "llm_model"):
        await db.execute("ALTER TABLE articles ADD COLUMN llm_model TEXT NOT NULL DEFAULT ''")
        migrated = True
    if not await _column_exists(db, "articles", "llm_reasoning_effort"):
        await db.execute(
            "ALTER TABLE articles ADD COLUMN llm_reasoning_effort TEXT NOT NULL DEFAULT ''"
        )
        migrated = True
    if not await _column_exists(db, "articles", "llm_generated_at"):
        await db.execute("ALTER TABLE articles ADD COLUMN llm_generated_at TEXT")
        migrated = True

    if not migrated:
        return

    await db.execute(
        """
        UPDATE articles
        SET llm_provider = 'unknown'
        WHERE llm_provider IS NULL OR trim(llm_provider) = ''
        """
    )
    await db.execute(
        """
        UPDATE articles
        SET llm_model = ''
        WHERE llm_model IS NULL
        """
    )
    await db.execute(
        """
        UPDATE articles
        SET llm_reasoning_effort = ''
        WHERE llm_reasoning_effort IS NULL
        """
    )
    await db.execute(
        """
        UPDATE articles
        SET llm_generated_at = COALESCE(NULLIF(trim(llm_generated_at), ''), created_at, datetime('now'))
        WHERE llm_generated_at IS NULL OR trim(llm_generated_at) = ''
        """
    )


async def _ensure_category_tables(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category_uid TEXT UNIQUE,
            name        TEXT NOT NULL UNIQUE,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            llm_enabled INTEGER NOT NULL DEFAULT 1,
            processing_stage TEXT NOT NULL DEFAULT 'off',
            is_default  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_categories_sort ON categories(sort_order ASC, id ASC)"
    )
    has_processing_stage = await _column_exists(db, "categories", "processing_stage")
    if not has_processing_stage:
        await db.execute(
            "ALTER TABLE categories ADD COLUMN processing_stage TEXT NOT NULL DEFAULT 'off'"
        )
        await db.execute(
            """
            UPDATE categories
            SET processing_stage = CASE
                WHEN llm_enabled = 1 THEN 'full'
                ELSE 'transcript_only'
            END
            """
        )
    if not await _column_exists(db, "categories", "category_uid"):
        await db.execute("ALTER TABLE categories ADD COLUMN category_uid TEXT")
    await db.execute(
        """
        UPDATE categories
        SET processing_stage = CASE lower(trim(coalesce(processing_stage, '')))
            WHEN 'off' THEN 'off'
            WHEN 'transcript_only' THEN 'transcript_only'
            WHEN 'full' THEN 'full'
            ELSE CASE WHEN llm_enabled = 1 THEN 'full' ELSE 'transcript_only' END
        END
        WHERE lower(trim(coalesce(processing_stage, ''))) NOT IN ('off', 'transcript_only', 'full')
        """
    )

    cursor = await db.execute("SELECT id FROM categories WHERE is_default = 1")
    row = await cursor.fetchone()
    if row is None:
        await db.execute(
            """
            INSERT INTO categories (category_uid, name, sort_order, llm_enabled, processing_stage, is_default)
            VALUES (?, '미분류', 0, 1, 'off', 1)
            """,
            (DEFAULT_CATEGORY_UID,),
        )
        logger.info(
            "event=db.default_category_created",
            extra={"event": "db.default_category_created"},
        )
    if not await _column_exists(db, "channels", "category_id"):
        await db.execute(
            "ALTER TABLE channels ADD COLUMN category_id INTEGER REFERENCES categories(id)"
        )
    default_cursor = await db.execute("SELECT id FROM categories WHERE is_default = 1")
    default_row = await default_cursor.fetchone()
    if default_row is not None:
        default_id = int(default_row["id"])
        await db.execute(
            "UPDATE categories SET category_uid = ? WHERE id = ?",
            (DEFAULT_CATEGORY_UID, default_id),
        )
        await db.execute(
            "UPDATE channels SET category_id = ? WHERE category_id IS NULL",
            (default_id,),
        )
    await db.execute(
        """
        UPDATE categories
        SET category_uid = 'category-' || id
        WHERE category_uid IS NULL OR trim(category_uid) = ''
        """
    )
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_category_uid ON categories(category_uid)"
    )


async def _ensure_download_columns(db: aiosqlite.Connection) -> None:
    if not await _column_exists(db, "download_jobs", "target_dir"):
        await db.execute("ALTER TABLE download_jobs ADD COLUMN target_dir TEXT")


async def _ensure_manual_transcript_jobs_table(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_transcript_jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id        TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
            status          TEXT NOT NULL DEFAULT 'pending',
            retry_count     INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            error_message   TEXT,
            language        TEXT,
            source_type     TEXT,
            requested_at    TEXT NOT NULL DEFAULT (datetime('now')),
            started_at      TEXT,
            finished_at     TEXT,
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_manual_transcript_jobs_status_requested
        ON manual_transcript_jobs(status, next_attempt_at, requested_at ASC, id ASC)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_manual_transcript_jobs_video_requested
        ON manual_transcript_jobs(video_id, requested_at DESC, id DESC)
        """
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_transcript_jobs_active_video
        ON manual_transcript_jobs(video_id)
        WHERE status IN ('pending', 'running')
        """
    )


async def _ensure_channel_metadata_columns(db: aiosqlite.Connection) -> None:
    if not await _column_exists(db, "channels", "last_seen_published_at"):
        await db.execute("ALTER TABLE channels ADD COLUMN last_seen_published_at TEXT")
    if not await _column_exists(db, "channels", "is_active"):
        await db.execute("ALTER TABLE channels ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    if not await _column_exists(db, "channels", "created_at"):
        await db.execute("ALTER TABLE channels ADD COLUMN created_at TEXT")
    await db.execute(
        """
        UPDATE channels
        SET created_at = datetime('now')
        WHERE created_at IS NULL OR trim(created_at) = ''
        """
    )
    if not await _column_exists(db, "channels", "channel_handle"):
        await db.execute("ALTER TABLE channels ADD COLUMN channel_handle TEXT")
    if not await _column_exists(db, "channels", "channel_url_canonical"):
        await db.execute("ALTER TABLE channels ADD COLUMN channel_url_canonical TEXT")
    if not await _column_exists(db, "channels", "channel_thumbnail_url"):
        await db.execute("ALTER TABLE channels ADD COLUMN channel_thumbnail_url TEXT")
    if not await _column_exists(db, "channels", "channel_description"):
        await db.execute("ALTER TABLE channels ADD COLUMN channel_description TEXT")
    if not await _column_exists(db, "channels", "channel_language_hint"):
        await db.execute("ALTER TABLE channels ADD COLUMN channel_language_hint TEXT")
    if not await _column_exists(db, "channels", "metadata_fetched_at"):
        await db.execute("ALTER TABLE channels ADD COLUMN metadata_fetched_at TEXT")
    if not await _column_exists(db, "channels", "metadata_fetch_status"):
        await db.execute("ALTER TABLE channels ADD COLUMN metadata_fetch_status TEXT")
    if not await _column_exists(db, "channels", "metadata_fetch_error"):
        await db.execute("ALTER TABLE channels ADD COLUMN metadata_fetch_error TEXT")
    if not await _column_exists(db, "channels", "metadata_retry_count"):
        await db.execute("ALTER TABLE channels ADD COLUMN metadata_retry_count INTEGER")
    if not await _column_exists(db, "channels", "metadata_next_fetch_at"):
        await db.execute("ALTER TABLE channels ADD COLUMN metadata_next_fetch_at TEXT")
    if not await _column_exists(db, "channels", "metadata_last_http_status"):
        await db.execute("ALTER TABLE channels ADD COLUMN metadata_last_http_status INTEGER")
    if not await _column_exists(db, "channels", "rss_fail_streak"):
        await db.execute(
            "ALTER TABLE channels ADD COLUMN rss_fail_streak INTEGER NOT NULL DEFAULT 0"
        )
    if not await _column_exists(db, "channels", "rss_last_polled_at"):
        await db.execute("ALTER TABLE channels ADD COLUMN rss_last_polled_at TEXT")
    if not await _column_exists(db, "channels", "rss_priority"):
        await db.execute(
            "ALTER TABLE channels ADD COLUMN rss_priority TEXT NOT NULL DEFAULT 'normal'"
        )
    if not await _column_exists(db, "channels", "rss_poll_interval_seconds"):
        await db.execute(
            "ALTER TABLE channels ADD COLUMN rss_poll_interval_seconds INTEGER NOT NULL DEFAULT 900"
        )
    if not await _column_exists(db, "channels", "rss_next_poll_at"):
        await db.execute("ALTER TABLE channels ADD COLUMN rss_next_poll_at TEXT")
    if not await _column_exists(db, "channels", "rss_last_etag"):
        await db.execute("ALTER TABLE channels ADD COLUMN rss_last_etag TEXT")
    if not await _column_exists(db, "channels", "rss_last_modified"):
        await db.execute("ALTER TABLE channels ADD COLUMN rss_last_modified TEXT")
    if not await _column_exists(db, "channels", "rss_cache_feed_mode"):
        await db.execute("ALTER TABLE channels ADD COLUMN rss_cache_feed_mode TEXT")

    await db.execute(
        """
        UPDATE channels
        SET metadata_fetch_status = 'never'
        WHERE metadata_fetch_status IS NULL OR trim(metadata_fetch_status) = ''
        """
    )
    await db.execute(
        """
        UPDATE channels
        SET metadata_fetch_status = CASE lower(trim(coalesce(metadata_fetch_status, '')))
            WHEN 'never' THEN 'never'
            WHEN 'pending' THEN 'pending'
            WHEN 'running' THEN 'running'
            WHEN 'success' THEN 'success'
            WHEN 'failed' THEN 'failed'
            WHEN 'rate_limited' THEN 'rate_limited'
            ELSE 'never'
        END
        """
    )
    await db.execute(
        """
        UPDATE channels
        SET metadata_retry_count = 0
        WHERE metadata_retry_count IS NULL OR metadata_retry_count < 0
        """
    )
    await db.execute(
        """
        UPDATE channels
        SET rss_priority = CASE lower(trim(coalesce(rss_priority, 'normal')))
            WHEN 'pinned' THEN 'pinned'
            WHEN 'low' THEN 'low'
            ELSE 'normal'
        END
        """
    )
    await db.execute(
        """
        UPDATE channels
        SET rss_poll_interval_seconds = CASE
            WHEN last_seen_published_at IS NOT NULL
             AND datetime(last_seen_published_at) >= datetime('now', '-2 days') THEN 450
            WHEN last_seen_published_at IS NOT NULL
             AND datetime(last_seen_published_at) >= datetime('now', '-14 days') THEN 900
            ELSE 3600
        END
        WHERE rss_poll_interval_seconds IS NULL
           OR rss_poll_interval_seconds < 300
           OR rss_poll_interval_seconds > 86400
           OR (rss_next_poll_at IS NULL AND rss_poll_interval_seconds = 900)
        """
    )
    await db.execute(
        """
        UPDATE channels
        SET rss_next_poll_at = CASE
            WHEN rss_last_polled_at IS NULL OR trim(rss_last_polled_at) = '' THEN datetime('now')
            ELSE datetime(rss_last_polled_at, '+' || rss_poll_interval_seconds || ' seconds')
        END
        WHERE is_active = 1
          AND (rss_next_poll_at IS NULL OR trim(rss_next_poll_at) = '')
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_channels_metadata_next_fetch
        ON channels(metadata_next_fetch_at, metadata_fetch_status)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_channels_metadata_status
        ON channels(metadata_fetch_status)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_channels_rss_next_poll
        ON channels(is_active, rss_next_poll_at, rss_priority)
        """
    )


async def get_schema_version(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return int(row[0] if row is not None else 0)


async def _migrate_to_v1(db: aiosqlite.Connection) -> None:
    await _ensure_app_settings_table(db)
    await _ensure_video_columns(db)
    await _ensure_article_columns(db)
    await _ensure_video_indexes(db)
    await _ensure_download_columns(db)
    await _ensure_manual_transcript_jobs_table(db)
    await _ensure_category_tables(db)
    await _ensure_channel_metadata_columns(db)
    for table in ("categories", "channels", "videos", "transcripts", "articles"):
        await _ensure_updated_at_column(db, table)


async def _drop_column_if_exists(db: aiosqlite.Connection, table: str, column: str) -> None:
    if await _column_exists(db, table, column):
        await db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


async def _purge_tombstoned_rows(db: aiosqlite.Connection) -> None:
    """Physically delete soft-deleted rows before dropping deleted_at."""
    article_columns = await _table_columns(db, "articles")
    transcript_columns = await _table_columns(db, "transcripts")
    video_columns = await _table_columns(db, "videos")
    channel_columns = await _table_columns(db, "channels")

    if "deleted_at" in article_columns:
        await db.execute("DELETE FROM articles WHERE deleted_at IS NOT NULL")
    if "deleted_at" in transcript_columns:
        await db.execute("DELETE FROM transcripts WHERE deleted_at IS NOT NULL")
    if "deleted_at" in video_columns:
        await db.execute("DELETE FROM videos WHERE deleted_at IS NOT NULL")

    # Tombstoned channels may still have non-tombstoned children; drop dependents first.
    if "deleted_at" in channel_columns:
        await db.execute(
            """
            DELETE FROM articles
            WHERE video_id IN (
                SELECT v.video_id
                FROM videos v
                JOIN channels c ON c.channel_id = v.channel_id
                WHERE c.deleted_at IS NOT NULL
            )
            """
        )
        await db.execute(
            """
            DELETE FROM transcripts
            WHERE video_id IN (
                SELECT v.video_id
                FROM videos v
                JOIN channels c ON c.channel_id = v.channel_id
                WHERE c.deleted_at IS NOT NULL
            )
            """
        )
        await db.execute(
            """
            DELETE FROM videos
            WHERE channel_id IN (
                SELECT channel_id FROM channels WHERE deleted_at IS NOT NULL
            )
            """
        )
        await db.execute("DELETE FROM channels WHERE deleted_at IS NOT NULL")

    cat_columns = await _table_columns(db, "categories")
    if "deleted_at" not in cat_columns:
        return
    default_cursor = await db.execute("SELECT id FROM categories WHERE is_default = 1 LIMIT 1")
    default_row = await default_cursor.fetchone()
    if default_row is not None:
        default_id = int(default_row["id"])
        await db.execute(
            """
            UPDATE channels
            SET category_id = ?
            WHERE category_id IN (
                SELECT id FROM categories
                WHERE deleted_at IS NOT NULL AND is_default = 0
            )
            """,
            (default_id,),
        )
    await db.execute("DELETE FROM categories WHERE deleted_at IS NOT NULL AND is_default = 0")


async def _migrate_to_v2(db: aiosqlite.Connection) -> None:
    await _purge_tombstoned_rows(db)
    # Drop indexes that reference soft-delete/sync columns before DROP COLUMN.
    await db.execute("DROP INDEX IF EXISTS idx_channels_rss_next_poll")
    for table in ("categories", "channels", "videos", "transcripts", "articles"):
        await db.execute(f"DROP INDEX IF EXISTS idx_{table}_sync_dirty_updated")
        for column in (
            "deleted_at",
            "sync_dirty",
            "sync_last_pushed_at",
            "origin_device_id",
        ):
            await _drop_column_if_exists(db, table, column)
    await db.execute(
        """
        DELETE FROM app_settings
        WHERE key IN (
            'remote_sync_device_id',
            'remote_sync_runtime_enabled',
            'remote_sync_last_success_at',
            'remote_sync_last_failure_code',
            'remote_sync_schema_version_status'
        )
        """
    )
    # Recreate rss poll index without deleted_at predicate when adaptive RSS columns exist.
    channel_columns = await _table_columns(db, "channels")
    if {"rss_next_poll_at", "rss_priority"}.issubset(channel_columns):
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channels_rss_next_poll
            ON channels(is_active, rss_next_poll_at, rss_priority)
            """
        )


async def _migrate_to_v3(db: aiosqlite.Connection) -> None:
    # Transcript text with [MM:SS] markers for grounded article timestamps.
    if not await _column_exists(db, "transcripts", "timed_text"):
        await db.execute("ALTER TABLE transcripts ADD COLUMN timed_text TEXT")


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    return await cursor.fetchone() is not None


async def _drop_manual_job_tables(db: aiosqlite.Connection) -> None:
    await db.execute("DROP TABLE IF EXISTS manual_article_jobs")
    await db.execute("DROP TABLE IF EXISTS manual_transcript_jobs")


_V4_VIDEOS_TABLE_SQL = """
    CREATE TABLE videos_new (
        video_id            TEXT PRIMARY KEY,
        channel_id          TEXT NOT NULL REFERENCES channels(channel_id),
        title               TEXT NOT NULL,
        upload_time         TEXT NOT NULL,
        thumbnail_path      TEXT,
        target_stage        TEXT NOT NULL DEFAULT 'article'
                            CHECK (target_stage IN ('none', 'transcript', 'article')),
        transcript_target_language TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        viewed_at           TEXT,
        updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )
"""

_V4_LEGACY_VIDEO_COLUMNS: tuple[tuple[str, str], ...] = (
    ("thumbnail_path", "TEXT"),
    ("pipeline_status", "TEXT"),
    ("processing_stage_snapshot", "TEXT"),
    ("transcript_retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("transcript_next_attempt_at", "TEXT"),
    ("transcript_target_language", "TEXT"),
    ("transcript_last_error", "TEXT"),
    ("transcript_last_error_at", "TEXT"),
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("created_at", "TEXT"),
    ("viewed_at", "TEXT"),
    ("updated_at", "TEXT"),
)

_V4_HAS_TRANSCRIPT = "EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.video_id)"
_V4_HAS_ARTICLE = "EXISTS (SELECT 1 FROM articles a WHERE a.video_id = v.video_id)"
_V4_STATUS = "lower(trim(coalesce(v.pipeline_status, '')))"


def _v4_no_job(kind: str) -> str:
    return (
        "NOT EXISTS (SELECT 1 FROM video_jobs j "
        f"WHERE j.video_id = v.video_id AND j.kind = '{kind}')"
    )


async def _v4_backfill_jobs(
    db: aiosqlite.Connection,
    *,
    has_manual_transcript_jobs: bool,
    has_manual_article_jobs: bool,
) -> None:
    """Translate v3 pipeline_status and active manual jobs into video_jobs rows.

    Runs before the old videos table is dropped. ``videos_new`` already holds the
    target stage. Active manual requests are newer intent than the auto queue, so
    they are inserted first and the legacy status only fills kinds without a job.
    """
    target = "(SELECT n.target_stage FROM videos_new n WHERE n.video_id = v.video_id)"
    if has_manual_transcript_jobs:
        await db.execute(
            f"""
            INSERT OR IGNORE INTO video_jobs(
                video_id, kind, origin, status, attempt_count, next_attempt_at, requested_at
            )
            SELECT m.video_id, 'transcript', 'manual', 'queued', m.retry_count,
                   m.next_attempt_at, m.requested_at
            FROM manual_transcript_jobs m
            JOIN videos v ON v.video_id = m.video_id
            WHERE m.status IN ('pending', 'running')
              AND NOT {_V4_HAS_TRANSCRIPT}
            ORDER BY m.id
            """
        )
    if has_manual_article_jobs:
        await db.execute(
            f"""
            INSERT OR IGNORE INTO video_jobs(video_id, kind, origin, status, requested_at)
            SELECT m.video_id,
                   CASE WHEN {_V4_HAS_TRANSCRIPT} THEN 'article' ELSE 'transcript' END,
                   'manual', 'queued', m.requested_at
            FROM manual_article_jobs m
            JOIN videos v ON v.video_id = m.video_id
            WHERE m.status IN ('pending', 'running')
            ORDER BY m.id
            """
        )

    await db.execute(
        f"""
        INSERT INTO video_jobs(
            video_id, kind, origin, status, attempt_count, next_attempt_at,
            error_code, error_message, error_at, finished_at
        )
        SELECT
            v.video_id,
            'transcript',
            'auto',
            CASE WHEN {_V4_STATUS} IN ('transcript_failed', 'no_subtitle') THEN 'failed'
                 ELSE 'queued' END,
            coalesce(v.transcript_retry_count, 0),
            CASE WHEN {_V4_STATUS} IN ('transcript_failed', 'no_subtitle') THEN NULL
                 ELSE v.transcript_next_attempt_at END,
            CASE
                WHEN {_V4_STATUS} = 'no_subtitle' THEN 'no_subtitle'
                WHEN {_V4_STATUS} = 'transcript_failed' THEN 'legacy_error'
                WHEN trim(coalesce(v.transcript_last_error, '')) != '' THEN 'legacy_error'
                ELSE NULL
            END,
            CASE WHEN {_V4_STATUS} = 'no_subtitle' THEN NULL ELSE v.transcript_last_error END,
            CASE WHEN {_V4_STATUS} = 'no_subtitle' THEN NULL ELSE v.transcript_last_error_at END,
            CASE WHEN {_V4_STATUS} IN ('transcript_failed', 'no_subtitle')
                 THEN coalesce(v.transcript_last_error_at, datetime('now')) END
        FROM videos v
        WHERE {_V4_STATUS} IN (
                'transcript_pending', 'transcript_processing', 'transcript_failed', 'no_subtitle'
            )
          AND {_v4_no_job("transcript")}
        """
    )
    await db.execute(
        f"""
        INSERT INTO video_jobs(
            video_id, kind, origin, status, attempt_count, error_code, finished_at
        )
        SELECT
            v.video_id,
            'article',
            'auto',
            CASE WHEN {_V4_STATUS} = 'manual_review' THEN 'failed' ELSE 'queued' END,
            coalesce(v.retry_count, 0),
            CASE
                WHEN {_V4_STATUS} = 'llm_failed' THEN 'legacy_error'
                WHEN {_V4_STATUS} = 'manual_review' AND {_V4_HAS_TRANSCRIPT}
                    THEN 'retry_exhausted'
                WHEN {_V4_STATUS} = 'manual_review' THEN 'transcript_missing'
                ELSE NULL
            END,
            CASE WHEN {_V4_STATUS} = 'manual_review' THEN datetime('now') END
        FROM videos v
        WHERE {_V4_STATUS} IN ('llm_pending', 'llm_processing', 'llm_failed', 'manual_review')
          AND {_v4_no_job("article")}
        """
    )
    # A cleared queue left auto_paused/transcript_done behind. Record it as a cancelled
    # job so the video reads as stopped short of its target instead of never queued.
    await db.execute(
        f"""
        INSERT INTO video_jobs(video_id, kind, origin, status, finished_at)
        SELECT v.video_id, 'transcript', 'auto', 'cancelled', datetime('now')
        FROM videos v
        WHERE {_V4_STATUS} IN ('auto_paused', 'transcript_done')
          AND {target} IN ('transcript', 'article')
          AND NOT {_V4_HAS_TRANSCRIPT}
          AND {_v4_no_job("transcript")}
        """
    )
    await db.execute(
        f"""
        INSERT INTO video_jobs(video_id, kind, origin, status, finished_at)
        SELECT v.video_id, 'article', 'auto', 'cancelled', datetime('now')
        FROM videos v
        WHERE {_V4_STATUS} IN ('auto_paused', 'transcript_done')
          AND {target} = 'article'
          AND {_V4_HAS_TRANSCRIPT}
          AND NOT {_V4_HAS_ARTICLE}
          AND {_v4_no_job("article")}
        """
    )


async def _migrate_to_v4(db: aiosqlite.Connection) -> None:
    """Split videos.pipeline_status into videos.target_stage and video_jobs."""
    video_columns = await _table_columns(db, "videos")
    if "target_stage" in video_columns:
        await _drop_manual_job_tables(db)
        return

    # Very old or hand-built legacy tables can miss optional columns the backfill reads.
    for column, ddl in _V4_LEGACY_VIDEO_COLUMNS:
        if column not in video_columns:
            await db.execute(f"ALTER TABLE videos ADD COLUMN {column} {ddl}")
    video_columns = await _table_columns(db, "videos")

    has_manual_transcript_jobs = await _table_exists(db, "manual_transcript_jobs")
    has_manual_article_jobs = await _table_exists(db, "manual_article_jobs")
    manual_article_target = (
        """
            WHEN EXISTS (
                SELECT 1 FROM manual_article_jobs m
                WHERE m.video_id = v.video_id AND m.status IN ('pending', 'running')
            ) THEN 'article'
        """
        if has_manual_article_jobs
        else ""
    )
    language_expr = _source_column_expr(video_columns, "transcript_target_language", "NULL")
    viewed_at_expr = _source_column_expr(video_columns, "viewed_at", "NULL")

    pragma_cursor = await db.execute("PRAGMA foreign_keys")
    pragma_row = await pragma_cursor.fetchone()
    foreign_keys_enabled = bool(int(pragma_row[0] if pragma_row is not None else 1))
    if foreign_keys_enabled:
        await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS video_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id        TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
                kind            TEXT NOT NULL CHECK (kind IN ('transcript', 'article')),
                origin          TEXT NOT NULL DEFAULT 'auto' CHECK (origin IN ('auto', 'manual')),
                status          TEXT NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
                attempt_count   INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                error_code      TEXT,
                error_message   TEXT,
                error_at        TEXT,
                requested_at    TEXT NOT NULL DEFAULT (datetime('now')),
                started_at      TEXT,
                finished_at     TEXT,
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_video_jobs_active
            ON video_jobs(video_id, kind)
            WHERE status IN ('queued', 'running')
            """
        )
        await db.execute(_V4_VIDEOS_TABLE_SQL)
        await db.execute(
            f"""
            INSERT INTO videos_new (
                video_id, channel_id, title, upload_time, thumbnail_path, target_stage,
                transcript_target_language, created_at, viewed_at, updated_at
            )
            SELECT
                v.video_id,
                v.channel_id,
                v.title,
                v.upload_time,
                v.thumbnail_path,
                CASE
                    WHEN {_V4_HAS_ARTICLE} THEN 'article'
                    WHEN {_V4_STATUS} IN (
                        'llm_pending', 'llm_processing', 'llm_failed', 'manual_review'
                    ) THEN 'article'
                    {manual_article_target}
                    ELSE CASE lower(trim(coalesce(v.processing_stage_snapshot, '')))
                        WHEN 'off' THEN 'none'
                        WHEN 'transcript_only' THEN 'transcript'
                        ELSE 'article'
                    END
                END,
                {language_expr},
                coalesce(v.created_at, datetime('now')),
                {viewed_at_expr},
                coalesce(NULLIF(trim(v.updated_at), ''), v.created_at, {UPDATED_AT_SQL})
            FROM videos v
            """
        )
        await _v4_backfill_jobs(
            db,
            has_manual_transcript_jobs=has_manual_transcript_jobs,
            has_manual_article_jobs=has_manual_article_jobs,
        )
        source_cursor = await db.execute("SELECT COUNT(1) AS cnt FROM videos")
        source_row = await source_cursor.fetchone()
        source_count = int(source_row["cnt"] if source_row is not None else 0)
        await db.execute("DROP TABLE videos")
        await db.execute("ALTER TABLE videos_new RENAME TO videos")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_videos_upload ON videos(upload_time DESC)")
        await _drop_manual_job_tables(db)
        target_cursor = await db.execute("SELECT COUNT(1) AS cnt FROM videos")
        target_row = await target_cursor.fetchone()
        target_count = int(target_row["cnt"] if target_row is not None else 0)
        if target_count != source_count:
            raise RuntimeError(
                f"videos row count changed during v4 migration: {source_count} -> {target_count}"
            )
        await db.commit()
        logger.info(
            "event=db.video_jobs_migration_completed videos=%s",
            target_count,
            extra={"event": "db.video_jobs_migration_completed"},
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "event=db.video_jobs_migration_failed",
            extra={"event": "db.video_jobs_migration_failed"},
        )
        raise
    finally:
        if foreign_keys_enabled:
            await db.execute("PRAGMA foreign_keys = ON")


MIGRATIONS = (
    (1, _migrate_to_v1),
    (2, _migrate_to_v2),
    (3, _migrate_to_v3),
    (4, _migrate_to_v4),
)

PIPELINE_VIEW_NAME = "video_pipeline_v"

# Derived pipeline state per video. Facts come from transcripts/articles, work from the
# newest video_jobs row per kind. Recreated on every start so it always matches the code.
_PIPELINE_VIEW_SQL = f"""
CREATE VIEW {PIPELINE_VIEW_NAME} AS
SELECT
    v.video_id,
    v.target_stage,
    t.video_id IS NOT NULL AS has_transcript,
    a.video_id IS NOT NULL AS has_article,
    tj.id AS transcript_job_id,
    tj.status AS transcript_job_status,
    tj.origin AS transcript_job_origin,
    coalesce(tj.attempt_count, 0) AS transcript_attempt_count,
    tj.next_attempt_at AS transcript_next_attempt_at,
    tj.error_code AS transcript_error_code,
    tj.error_message AS transcript_error_message,
    tj.error_at AS transcript_error_at,
    tj.requested_at AS transcript_requested_at,
    tj.started_at AS transcript_started_at,
    tj.finished_at AS transcript_finished_at,
    aj.id AS article_job_id,
    aj.status AS article_job_status,
    aj.origin AS article_job_origin,
    coalesce(aj.attempt_count, 0) AS article_attempt_count,
    aj.error_code AS article_error_code,
    aj.error_message AS article_error_message,
    aj.error_at AS article_error_at,
    CASE
        WHEN a.video_id IS NOT NULL THEN 'article_ready'
        WHEN aj.status IN ('queued', 'running') THEN 'article_pending'
        WHEN t.video_id IS NOT NULL AND aj.status = 'failed' THEN 'article_failed'
        WHEN t.video_id IS NOT NULL AND v.target_stage = 'article' THEN 'paused'
        WHEN t.video_id IS NOT NULL THEN 'transcript_ready'
        WHEN tj.status IN ('queued', 'running') THEN 'transcript_pending'
        WHEN tj.status = 'failed' AND tj.error_code = 'no_subtitle' THEN 'no_subtitle'
        WHEN tj.status = 'failed' THEN 'transcript_failed'
        WHEN aj.status = 'failed' THEN 'article_failed'
        ELSE 'paused'
    END AS pipeline_status,
    CASE
        WHEN aj.status = 'running' THEN 'running'
        WHEN aj.status = 'queued' AND aj.error_code IS NOT NULL THEN 'retry_wait'
        WHEN aj.status = 'queued' THEN 'queued'
        WHEN tj.status = 'running' THEN 'running'
        WHEN tj.status = 'queued' AND tj.error_code IS NOT NULL THEN 'retry_wait'
        WHEN tj.status = 'queued' THEN 'queued'
        ELSE 'idle'
    END AS pipeline_activity,
    CASE
        WHEN aj.status IN ('queued', 'running') THEN 'article'
        WHEN tj.status IN ('queued', 'running') THEN 'transcript'
    END AS activity_kind
FROM videos v
LEFT JOIN transcripts t ON t.video_id = v.video_id
LEFT JOIN articles a ON a.video_id = v.video_id
LEFT JOIN video_jobs tj ON tj.id = (
    SELECT max(j.id) FROM video_jobs j WHERE j.video_id = v.video_id AND j.kind = 'transcript'
)
LEFT JOIN video_jobs aj ON aj.id = (
    SELECT max(j.id) FROM video_jobs j WHERE j.video_id = v.video_id AND j.kind = 'article'
)
"""


async def _ensure_pipeline_view(db: aiosqlite.Connection) -> None:
    await db.execute(f"DROP VIEW IF EXISTS {PIPELINE_VIEW_NAME}")
    await db.execute(_PIPELINE_VIEW_SQL)
    await db.commit()


async def run_database_migrations(db: aiosqlite.Connection) -> None:
    current_version = await get_schema_version(db)
    if current_version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {current_version} is newer than supported version "
            f"{CURRENT_SCHEMA_VERSION}"
        )
    # Table rebuilds rename videos; a view over the old table would block the rename.
    await db.execute(f"DROP VIEW IF EXISTS {PIPELINE_VIEW_NAME}")
    await db.commit()
    for target_version, migration in MIGRATIONS:
        if current_version >= target_version:
            continue
        await migration(db)
        await db.execute(f"PRAGMA user_version = {target_version}")
        await db.commit()
        current_version = target_version
    await _ensure_pipeline_view(db)
