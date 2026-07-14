from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import aiosqlite

from app.remote_sync_metadata import (
    SYNC_NOW_SQL,
    is_remote_sync_runtime_enabled,
    sync_dirty_set_clause,
)
from app.repositories import (
    _alerts_retention as alerts_repository,
)
from app.repositories import (
    _categories as categories_repository,
)

logger = logging.getLogger(__name__)

CHANNEL_MANAGEMENT_STATUS_ACTIVE = "active"
CHANNEL_MANAGEMENT_STATUS_INACTIVE = "inactive"
CHANNEL_MANAGEMENT_STATUS_OPTIONS = {
    CHANNEL_MANAGEMENT_STATUS_ACTIVE,
    CHANNEL_MANAGEMENT_STATUS_INACTIVE,
}

CHANNEL_METADATA_STATUS_NEVER = "never"
CHANNEL_METADATA_STATUS_PENDING = "pending"
CHANNEL_METADATA_STATUS_RUNNING = "running"
CHANNEL_METADATA_STATUS_SUCCESS = "success"
CHANNEL_METADATA_STATUS_FAILED = "failed"
CHANNEL_METADATA_STATUS_RATE_LIMITED = "rate_limited"
CHANNEL_METADATA_STATUS_OPTIONS = {
    CHANNEL_METADATA_STATUS_NEVER,
    CHANNEL_METADATA_STATUS_PENDING,
    CHANNEL_METADATA_STATUS_RUNNING,
    CHANNEL_METADATA_STATUS_SUCCESS,
    CHANNEL_METADATA_STATUS_FAILED,
    CHANNEL_METADATA_STATUS_RATE_LIMITED,
}
CHANNEL_METADATA_REFRESH_INTERVAL_DAYS_DEFAULT = 30
CHANNEL_METADATA_RATE_LIMIT_BACKOFF_MINUTES = (360, 720, 1440)
CHANNEL_METADATA_FAILURE_BACKOFF_MINUTES = (15, 30, 60, 180)
CHANNEL_METADATA_MAX_RETRY_COUNT = 12


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{k: row[k] for k in row.keys()} for row in rows]


def normalize_channel_metadata_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CHANNEL_METADATA_STATUS_OPTIONS:
        return normalized
    return CHANNEL_METADATA_STATUS_NEVER


def _normalize_optional_text(value: object | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if len(raw) > max_length:
        return raw[:max_length]
    return raw


def _normalize_optional_int(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


async def list_channels(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            rss_priority,
            rss_poll_interval_seconds,
            rss_next_poll_at,
            rss_last_etag,
            rss_last_modified,
            rss_cache_feed_mode,
            channel_handle,
            channel_url_canonical,
            channel_thumbnail_url,
            channel_description,
            channel_language_hint,
            metadata_fetched_at,
            metadata_fetch_status,
            metadata_fetch_error,
            metadata_retry_count,
            metadata_next_fetch_at,
            metadata_last_http_status,
            category_id,
            created_at
        FROM channels
        WHERE deleted_at IS NULL
        ORDER BY created_at DESC
        """
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


def normalize_channel_management_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CHANNEL_MANAGEMENT_STATUS_OPTIONS:
        return normalized
    return CHANNEL_MANAGEMENT_STATUS_ACTIVE


async def list_channels_for_management(
    db: aiosqlite.Connection,
    status: str = CHANNEL_MANAGEMENT_STATUS_ACTIVE,
    category_id: int | None = None,
) -> list[dict[str, Any]]:
    normalized_status = normalize_channel_management_status(status)
    is_active = 1 if normalized_status == CHANNEL_MANAGEMENT_STATUS_ACTIVE else 0
    params: list[object] = [alerts_repository.ALERT_TYPE_RSS_CHANNEL_NOT_FOUND, is_active]
    category_filter = ""
    if category_id is not None:
        category_filter = "AND c.category_id = ?"
        params.append(category_id)
    cursor = await db.execute(
        f"""
        SELECT
            c.channel_id,
            c.channel_name,
            c.rss_url,
            c.is_active,
            c.last_seen_published_at,
            c.rss_priority,
            c.rss_poll_interval_seconds,
            c.rss_next_poll_at,
            c.rss_last_etag,
            c.rss_last_modified,
            c.rss_cache_feed_mode,
            c.category_id,
            c.channel_handle,
            c.channel_url_canonical,
            c.channel_thumbnail_url,
            c.channel_description,
            c.channel_language_hint,
            c.metadata_fetched_at,
            c.metadata_fetch_status,
            c.metadata_fetch_error,
            c.metadata_retry_count,
            c.metadata_next_fetch_at,
            c.metadata_last_http_status,
            c.created_at,
            cat.name AS category_name,
            COALESCE(cat.is_default, 0) AS category_is_default,
            sa.message AS inactive_reason,
            sa.created_at AS inactive_at
        FROM channels c
        LEFT JOIN categories cat ON cat.id = c.category_id
        LEFT JOIN system_alerts sa
          ON sa.id = (
              SELECT s2.id
              FROM system_alerts s2
              WHERE s2.channel_id = c.channel_id
                AND s2.alert_type = ?
              ORDER BY s2.created_at DESC, s2.id DESC
              LIMIT 1
          )
        WHERE c.is_active = ?
          AND c.deleted_at IS NULL
        {category_filter}
        ORDER BY c.created_at DESC
        """,
        tuple(params),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def get_channel_name_map(
    db: aiosqlite.Connection,
    channel_ids: list[str],
) -> dict[str, str]:
    normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not normalized:
        return {}
    placeholders = ",".join(["?"] * len(normalized))
    cursor = await db.execute(
        f"""
        SELECT channel_id, channel_name
        FROM channels
        WHERE channel_id IN ({placeholders})
          AND deleted_at IS NULL
        """,
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    return {str(row["channel_id"]): str(row["channel_name"] or row["channel_id"]) for row in rows}


async def count_channels_by_status(db: aiosqlite.Connection) -> dict[str, int]:
    cursor = await db.execute(
        """
        SELECT
            SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END) AS inactive_count
        FROM channels
        WHERE deleted_at IS NULL
        """
    )
    row = await cursor.fetchone()
    active_count = int((row["active_count"] if row else 0) or 0)
    inactive_count = int((row["inactive_count"] if row else 0) or 0)
    return {
        CHANNEL_MANAGEMENT_STATUS_ACTIVE: active_count,
        CHANNEL_MANAGEMENT_STATUS_INACTIVE: inactive_count,
    }


async def add_channel(
    db: aiosqlite.Connection,
    channel_id: str,
    channel_name: str,
    category_id: int | None = None,
    *,
    channel_handle: str | None = None,
    channel_url_canonical: str | None = None,
    channel_thumbnail_url: str | None = None,
    channel_description: str | None = None,
    channel_language_hint: str | None = None,
    metadata_fetch_status: str | None = None,
    metadata_fetch_error: str | None = None,
    metadata_retry_count: int | None = None,
    metadata_next_fetch_at: str | None = None,
    metadata_last_http_status: int | None = None,
    metadata_fetched_at: str | None = None,
) -> dict[str, Any]:
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    if category_id is None:
        category_id = await categories_repository.get_default_category_id(db)
    safe_handle = _normalize_optional_text(channel_handle, max_length=128)
    safe_canonical_url = _normalize_optional_text(channel_url_canonical, max_length=512)
    safe_thumbnail_url = _normalize_optional_text(channel_thumbnail_url, max_length=512)
    safe_description = _normalize_optional_text(channel_description, max_length=4000)
    safe_language_hint = _normalize_optional_text(channel_language_hint, max_length=32)
    safe_fetch_status = (
        normalize_channel_metadata_status(metadata_fetch_status)
        if metadata_fetch_status is not None
        else None
    )
    safe_fetch_error = _normalize_optional_text(metadata_fetch_error, max_length=512)
    safe_retry_count = _normalize_optional_int(metadata_retry_count)
    safe_next_fetch_at = _normalize_optional_text(metadata_next_fetch_at, max_length=64)
    safe_last_http_status = _normalize_optional_int(metadata_last_http_status)
    safe_fetched_at = _normalize_optional_text(metadata_fetched_at, max_length=64)
    await db.execute(
        f"""
        INSERT INTO channels (
            channel_id,
            channel_name,
            rss_url,
            is_active,
            category_id,
            created_at,
            sync_dirty,
            origin_device_id
        )
        VALUES (?, ?, ?, 1, ?, datetime('now'), 1, COALESCE((SELECT value FROM app_settings WHERE key = 'remote_sync_device_id'), ''))
        ON CONFLICT(channel_id) DO UPDATE SET
            channel_name=excluded.channel_name,
            rss_url=excluded.rss_url,
            is_active=1,
            category_id=excluded.category_id,
            deleted_at=NULL,
            created_at=COALESCE(channels.created_at, datetime('now')),
            updated_at={SYNC_NOW_SQL},
            sync_dirty=1,
            origin_device_id=excluded.origin_device_id
        """,
        (
            channel_id,
            channel_name,
            rss_url,
            category_id,
        ),
    )
    await db.execute(
        """
        UPDATE channels
        SET created_at = datetime('now')
        WHERE channel_id = ?
          AND (created_at IS NULL OR trim(created_at) = '')
        """,
        (channel_id,),
    )
    if any(
        value is not None
        for value in (
            safe_handle,
            safe_canonical_url,
            safe_thumbnail_url,
            safe_description,
            safe_language_hint,
            safe_fetch_status,
            safe_fetch_error,
            safe_retry_count,
            safe_next_fetch_at,
            safe_last_http_status,
            safe_fetched_at,
        )
    ):
        await db.execute(
            """
            UPDATE channels
            SET
                channel_handle=COALESCE(?, channel_handle),
                channel_url_canonical=COALESCE(?, channel_url_canonical),
                channel_thumbnail_url=COALESCE(?, channel_thumbnail_url),
                channel_description=COALESCE(?, channel_description),
                channel_language_hint=COALESCE(?, channel_language_hint),
                metadata_fetched_at=COALESCE(?, metadata_fetched_at),
                metadata_fetch_status=COALESCE(?, metadata_fetch_status),
                metadata_fetch_error=COALESCE(?, metadata_fetch_error),
                metadata_retry_count=COALESCE(?, metadata_retry_count),
                metadata_next_fetch_at=COALESCE(?, metadata_next_fetch_at),
                metadata_last_http_status=COALESCE(?, metadata_last_http_status),
                sync_dirty=1,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                origin_device_id=COALESCE((SELECT value FROM app_settings WHERE key = 'remote_sync_device_id'), origin_device_id, '')
            WHERE channel_id = ?
              AND deleted_at IS NULL
            """,
            (
                safe_handle,
                safe_canonical_url,
                safe_thumbnail_url,
                safe_description,
                safe_language_hint,
                safe_fetched_at,
                safe_fetch_status,
                safe_fetch_error,
                safe_retry_count,
                safe_next_fetch_at,
                safe_last_http_status,
                channel_id,
            ),
        )
    await db.commit()

    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            category_id,
            rss_priority,
            rss_poll_interval_seconds,
            rss_next_poll_at,
            rss_last_etag,
            rss_last_modified,
            rss_cache_feed_mode,
            channel_handle,
            channel_url_canonical,
            channel_thumbnail_url,
            channel_description,
            channel_language_hint,
            metadata_fetched_at,
            metadata_fetch_status,
            metadata_fetch_error,
            metadata_retry_count,
            metadata_next_fetch_at,
            metadata_last_http_status,
            created_at
        FROM channels
        WHERE channel_id = ?
          AND deleted_at IS NULL
        """,
        (channel_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row) or {}


async def get_channel_by_id(db: aiosqlite.Connection, channel_id: str) -> dict[str, Any] | None:
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id:
        return None
    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            category_id,
            rss_priority,
            rss_poll_interval_seconds,
            rss_next_poll_at,
            rss_last_etag,
            rss_last_modified,
            rss_cache_feed_mode,
            channel_handle,
            channel_url_canonical,
            channel_thumbnail_url,
            channel_description,
            channel_language_hint,
            metadata_fetched_at,
            metadata_fetch_status,
            metadata_fetch_error,
            metadata_retry_count,
            metadata_next_fetch_at,
            metadata_last_http_status,
            created_at
        FROM channels
        WHERE channel_id = ?
          AND deleted_at IS NULL
        LIMIT 1
        """,
        (normalized_channel_id,),
    )
    return _row_to_dict(await cursor.fetchone())


async def deactivate_channel(db: aiosqlite.Connection, channel_id: str) -> int:
    cursor = await db.execute(
        f"""
        UPDATE channels
        SET is_active = 0,
            {sync_dirty_set_clause()}
        WHERE channel_id = ?
          AND deleted_at IS NULL
        """,
        (channel_id,),
    )
    await db.commit()
    return cursor.rowcount


async def reactivate_channel(db: aiosqlite.Connection, channel_id: str) -> int:
    cursor = await db.execute(
        f"""
        UPDATE channels
        SET
            is_active = 1,
            rss_fail_streak = 0,
            rss_next_poll_at = datetime('now'),
            {sync_dirty_set_clause()}
        WHERE channel_id = ?
          AND deleted_at IS NULL
        """,
        (channel_id,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def reactivate_channels(db: aiosqlite.Connection, channel_ids: list[str]) -> int:
    normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not normalized:
        return 0
    placeholders = ",".join(["?"] * len(normalized))
    cursor = await db.execute(
        f"""
        UPDATE channels
        SET
            is_active = 1,
            rss_fail_streak = 0,
            rss_next_poll_at = datetime('now'),
            {sync_dirty_set_clause()}
        WHERE channel_id IN ({placeholders})
          AND deleted_at IS NULL
        """,
        tuple(normalized),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def list_active_channels(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            metadata_fetched_at,
            metadata_fetch_status,
            metadata_next_fetch_at,
            rss_priority,
            rss_poll_interval_seconds,
            rss_next_poll_at,
            rss_last_etag,
            rss_last_modified,
            rss_cache_feed_mode,
            created_at
        FROM channels
        WHERE is_active = 1
          AND deleted_at IS NULL
        ORDER BY created_at ASC
        """
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def update_channel_watermark(
    db: aiosqlite.Connection, channel_id: str, published_at: str
) -> None:
    await db.execute(
        f"""
        UPDATE channels
        SET last_seen_published_at = ?,
            {sync_dirty_set_clause()}
        WHERE channel_id = ?
          AND deleted_at IS NULL
        """,
        (published_at, channel_id),
    )
    await db.commit()


async def delete_channels_with_related_data(
    db: aiosqlite.Connection,
    channel_ids: list[str],
) -> dict[str, Any]:
    normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not normalized:
        return {"deleted_channels": 0, "deleted_videos": 0, "thumbnail_paths": []}

    placeholders = ",".join(["?"] * len(normalized))

    cursor = await db.execute(
        f"""
        SELECT thumbnail_path
        FROM videos
        WHERE channel_id IN ({placeholders})
          AND deleted_at IS NULL
        """,
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    thumbnail_paths = [str(row["thumbnail_path"]) for row in rows if row["thumbnail_path"]]

    if await is_remote_sync_runtime_enabled(db):
        video_cursor = await db.execute(
            f"""
            SELECT video_id
            FROM videos
            WHERE channel_id IN ({placeholders})
              AND deleted_at IS NULL
            """,
            tuple(normalized),
        )
        video_rows = await video_cursor.fetchall()
        video_ids = [str(row["video_id"]) for row in video_rows]
        if video_ids:
            video_placeholders = ",".join(["?"] * len(video_ids))
            await db.execute(
                f"""
                UPDATE articles
                SET deleted_at = COALESCE(deleted_at, {SYNC_NOW_SQL}),
                    {sync_dirty_set_clause()}
                WHERE video_id IN ({video_placeholders})
                  AND deleted_at IS NULL
                """,
                tuple(video_ids),
            )
            await db.execute(
                f"""
                UPDATE transcripts
                SET deleted_at = COALESCE(deleted_at, {SYNC_NOW_SQL}),
                    {sync_dirty_set_clause()}
                WHERE video_id IN ({video_placeholders})
                  AND deleted_at IS NULL
                """,
                tuple(video_ids),
            )
        videos_cursor = await db.execute(
            f"""
            UPDATE videos
            SET deleted_at = COALESCE(deleted_at, {SYNC_NOW_SQL}),
                {sync_dirty_set_clause()}
            WHERE channel_id IN ({placeholders})
              AND deleted_at IS NULL
            """,
            tuple(normalized),
        )
        channels_cursor = await db.execute(
            f"""
            UPDATE channels
            SET deleted_at = COALESCE(deleted_at, {SYNC_NOW_SQL}),
                {sync_dirty_set_clause()}
            WHERE channel_id IN ({placeholders})
              AND deleted_at IS NULL
            """,
            tuple(normalized),
        )
    else:
        await db.execute(
            f"""
            DELETE FROM articles
            WHERE video_id IN (
                SELECT video_id FROM videos WHERE channel_id IN ({placeholders})
            )
            """,
            tuple(normalized),
        )
        await db.execute(
            f"""
            DELETE FROM transcripts
            WHERE video_id IN (
                SELECT video_id FROM videos WHERE channel_id IN ({placeholders})
            )
            """,
            tuple(normalized),
        )
        videos_cursor = await db.execute(
            f"DELETE FROM videos WHERE channel_id IN ({placeholders})",
            tuple(normalized),
        )
        channels_cursor = await db.execute(
            f"DELETE FROM channels WHERE channel_id IN ({placeholders})",
            tuple(normalized),
        )
    await db.commit()

    return {
        "deleted_channels": int(channels_cursor.rowcount or 0),
        "deleted_videos": int(videos_cursor.rowcount or 0),
        "thumbnail_paths": thumbnail_paths,
    }
