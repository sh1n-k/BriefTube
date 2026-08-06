from __future__ import annotations

from typing import Any

import aiosqlite

from app.repositories._channels import (
    CHANNEL_MANAGEMENT_STATUS_ACTIVE,
    CHANNEL_MANAGEMENT_STATUS_OPTIONS,
    CHANNEL_METADATA_FAILURE_BACKOFF_MINUTES,
    CHANNEL_METADATA_MAX_RETRY_COUNT,
    CHANNEL_METADATA_RATE_LIMIT_BACKOFF_MINUTES,
    CHANNEL_METADATA_REFRESH_INTERVAL_DAYS_DEFAULT,
    CHANNEL_METADATA_STATUS_FAILED,
    CHANNEL_METADATA_STATUS_NEVER,
    CHANNEL_METADATA_STATUS_PENDING,
    CHANNEL_METADATA_STATUS_RATE_LIMITED,
    CHANNEL_METADATA_STATUS_RUNNING,
    CHANNEL_METADATA_STATUS_SUCCESS,
    _normalize_optional_int,  # pyright: ignore[reportPrivateUsage]
    _normalize_optional_text,  # pyright: ignore[reportPrivateUsage]
)
from app.repositories._common import UPDATED_AT_SQL
from app.repositories._common import row_to_dict as _row_to_dict


def _next_metadata_backoff_minutes(
    *,
    retry_count: int,
    is_rate_limited: bool,
) -> int:
    schedule = (
        CHANNEL_METADATA_RATE_LIMIT_BACKOFF_MINUTES
        if is_rate_limited
        else CHANNEL_METADATA_FAILURE_BACKOFF_MINUTES
    )
    idx = max(0, min(len(schedule) - 1, retry_count))
    return int(schedule[idx])


async def enqueue_channel_metadata_refresh(
    db: aiosqlite.Connection,
    *,
    channel_id: str,
) -> int:
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id:
        return 0
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL,
            metadata_next_fetch_at = datetime('now')
        WHERE channel_id = ?
        """,
        (CHANNEL_METADATA_STATUS_PENDING, normalized_channel_id),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def schedule_channel_metadata_backfill(
    db: aiosqlite.Connection,
    *,
    stale_days: int = CHANNEL_METADATA_REFRESH_INTERVAL_DAYS_DEFAULT,
) -> int:
    safe_stale_days = max(1, int(stale_days))
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL,
            metadata_next_fetch_at = datetime('now')
        WHERE metadata_fetch_status != ?
          AND (
            metadata_fetched_at IS NULL
            OR metadata_next_fetch_at IS NULL
            OR metadata_next_fetch_at <= datetime('now')
            OR metadata_fetch_status IN (?, ?, ?)
            OR (julianday('now') - julianday(metadata_fetched_at)) >= ?
          )
        """,
        (
            CHANNEL_METADATA_STATUS_PENDING,
            CHANNEL_METADATA_STATUS_RUNNING,
            CHANNEL_METADATA_STATUS_NEVER,
            CHANNEL_METADATA_STATUS_FAILED,
            CHANNEL_METADATA_STATUS_RATE_LIMITED,
            safe_stale_days,
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def claim_next_channel_metadata_target(db: aiosqlite.Connection) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT channel_id
        FROM channels
        WHERE metadata_fetch_status = ?
          AND (
            metadata_next_fetch_at IS NULL
            OR metadata_next_fetch_at <= datetime('now')
          )
        ORDER BY COALESCE(metadata_next_fetch_at, datetime('now')) ASC, created_at ASC
        LIMIT 1
        """,
        (CHANNEL_METADATA_STATUS_PENDING,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    channel_id = str(row["channel_id"])
    updated = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL
        WHERE channel_id = ?
          AND metadata_fetch_status = ?
        """,
        (
            CHANNEL_METADATA_STATUS_RUNNING,
            channel_id,
            CHANNEL_METADATA_STATUS_PENDING,
        ),
    )
    if int(updated.rowcount or 0) == 0:
        return None
    await db.commit()
    detail_cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            metadata_retry_count,
            metadata_next_fetch_at
        FROM channels
        WHERE channel_id = ?
        LIMIT 1
        """,
        (channel_id,),
    )
    return _row_to_dict(await detail_cursor.fetchone())


async def mark_channel_metadata_succeeded(
    db: aiosqlite.Connection,
    *,
    channel_id: str,
    channel_name: str | None,
    channel_handle: str | None,
    channel_url_canonical: str | None,
    channel_thumbnail_url: str | None,
    channel_description: str | None,
    channel_language_hint: str | None,
    http_status: int | None = None,
    refresh_interval_days: int = CHANNEL_METADATA_REFRESH_INTERVAL_DAYS_DEFAULT,
) -> int:
    safe_channel_name = _normalize_optional_text(channel_name, max_length=255)
    safe_handle = _normalize_optional_text(channel_handle, max_length=128)
    safe_canonical_url = _normalize_optional_text(channel_url_canonical, max_length=512)
    safe_thumbnail_url = _normalize_optional_text(channel_thumbnail_url, max_length=512)
    safe_description = _normalize_optional_text(channel_description, max_length=4000)
    safe_language_hint = _normalize_optional_text(channel_language_hint, max_length=32)
    safe_http_status = _normalize_optional_int(http_status)
    safe_interval_days = max(1, int(refresh_interval_days))
    cursor = await db.execute(
        f"""
        UPDATE channels
        SET
            channel_name = COALESCE(?, channel_name),
            channel_handle = ?,
            channel_url_canonical = ?,
            channel_thumbnail_url = ?,
            channel_description = ?,
            channel_language_hint = ?,
            metadata_fetched_at = datetime('now'),
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL,
            metadata_retry_count = 0,
            metadata_next_fetch_at = datetime('now', '+' || ? || ' days'),
            metadata_last_http_status = ?,
            updated_at = {UPDATED_AT_SQL}
        WHERE channel_id = ?
        """,
        (
            safe_channel_name,
            safe_handle,
            safe_canonical_url,
            safe_thumbnail_url,
            safe_description,
            safe_language_hint,
            CHANNEL_METADATA_STATUS_SUCCESS,
            safe_interval_days,
            safe_http_status,
            channel_id,
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_channel_metadata_failed(
    db: aiosqlite.Connection,
    *,
    channel_id: str,
    error_message: str,
    http_status: int | None = None,
    is_rate_limited: bool = False,
) -> int:
    row_cursor = await db.execute(
        """
        SELECT metadata_retry_count
        FROM channels
        WHERE channel_id = ?
        LIMIT 1
        """,
        (channel_id,),
    )
    row = await row_cursor.fetchone()
    if row is None:
        return 0
    previous_retry_count = int(row["metadata_retry_count"] or 0)
    retry_count = min(CHANNEL_METADATA_MAX_RETRY_COUNT, max(0, previous_retry_count + 1))
    backoff_minutes = _next_metadata_backoff_minutes(
        retry_count=retry_count - 1,
        is_rate_limited=is_rate_limited,
    )
    safe_http_status = _normalize_optional_int(http_status)
    status = (
        CHANNEL_METADATA_STATUS_RATE_LIMITED if is_rate_limited else CHANNEL_METADATA_STATUS_FAILED
    )
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = ?,
            metadata_retry_count = ?,
            metadata_next_fetch_at = datetime('now', '+' || ? || ' minutes'),
            metadata_last_http_status = ?
        WHERE channel_id = ?
        """,
        (
            status,
            _normalize_optional_text(error_message, max_length=512),
            retry_count,
            backoff_minutes,
            safe_http_status,
            channel_id,
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def enqueue_failed_channel_metadata(
    db: aiosqlite.Connection,
    *,
    status: str | None = None,
    category_id: int | None = None,
) -> int:
    where_clauses = ["metadata_fetch_status IN (?, ?, ?)"]
    params: list[Any] = [
        CHANNEL_METADATA_STATUS_PENDING,
        CHANNEL_METADATA_STATUS_PENDING,
        CHANNEL_METADATA_STATUS_FAILED,
        CHANNEL_METADATA_STATUS_RATE_LIMITED,
    ]
    normalized_status = str(status or "").strip().lower()
    if normalized_status in CHANNEL_MANAGEMENT_STATUS_OPTIONS:
        where_clauses.append("is_active = ?")
        params.append(1 if normalized_status == CHANNEL_MANAGEMENT_STATUS_ACTIVE else 0)
    if category_id is not None:
        where_clauses.append("category_id = ?")
        params.append(int(category_id))
    where_sql = " AND ".join(where_clauses)
    cursor = await db.execute(
        f"""
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL,
            metadata_next_fetch_at = datetime('now')
        WHERE {where_sql}
        """,
        tuple(params),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def recover_stuck_channel_metadata_running(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = COALESCE(metadata_fetch_error, 'metadata worker interrupted'),
            metadata_next_fetch_at = datetime('now', '+15 minutes')
        WHERE metadata_fetch_status = ?
        """,
        (
            CHANNEL_METADATA_STATUS_FAILED,
            CHANNEL_METADATA_STATUS_RUNNING,
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)
