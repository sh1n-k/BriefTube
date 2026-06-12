from __future__ import annotations

from datetime import datetime
from typing import Any

import aiosqlite

from app.remote_sync_metadata import sync_dirty_set_clause
from app.repositories._channels import (
    _row_to_dict,  # pyright: ignore[reportPrivateUsage]
)

RSS_PRIORITY_PINNED = "pinned"
RSS_PRIORITY_NORMAL = "normal"
RSS_PRIORITY_LOW = "low"
RSS_PRIORITY_OPTIONS = {RSS_PRIORITY_PINNED, RSS_PRIORITY_NORMAL, RSS_PRIORITY_LOW}


def normalize_rss_priority(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in RSS_PRIORITY_OPTIONS:
        return normalized
    return RSS_PRIORITY_NORMAL


async def pick_next_rss_channel(
    db: aiosqlite.Connection,
    *,
    include_not_due: bool = False,
    exclude_channel_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    due_filter = ""
    if not include_not_due:
        due_filter = """
          AND (
              rss_next_poll_at IS NULL
              OR trim(rss_next_poll_at) = ''
              OR datetime(rss_next_poll_at) <= datetime('now')
          )
        """
    normalized_excluded = [
        channel_id for channel_id in dict.fromkeys(exclude_channel_ids or []) if channel_id
    ]
    exclude_filter = ""
    params: tuple[str, ...] = ()
    if normalized_excluded:
        placeholders = ",".join(["?"] * len(normalized_excluded))
        exclude_filter = f"AND channel_id NOT IN ({placeholders})"
        params = tuple(normalized_excluded)
    cursor = await db.execute(
        f"""
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            rss_fail_streak,
            rss_last_polled_at,
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
        {due_filter}
        {exclude_filter}
        ORDER BY
            CASE WHEN rss_next_poll_at IS NULL THEN 0 ELSE 1 END,
            datetime(rss_next_poll_at) ASC,
            CASE rss_priority
                WHEN 'pinned' THEN 0
                WHEN 'normal' THEN 1
                ELSE 2
            END,
            created_at ASC
        LIMIT 1
        """,
        params,
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def mark_rss_poll_success(
    db: aiosqlite.Connection,
    channel_id: str,
    *,
    interval_seconds: int,
    etag: str | None = None,
    last_modified: str | None = None,
    feed_mode: str | None = None,
) -> None:
    await db.execute(
        """
        UPDATE channels
        SET rss_fail_streak = 0,
            rss_last_polled_at = datetime('now'),
            rss_poll_interval_seconds = ?,
            rss_next_poll_at = datetime('now', '+' || ? || ' seconds'),
            rss_last_etag = ?,
            rss_last_modified = ?,
            rss_cache_feed_mode = ?
        WHERE channel_id = ?
        """,
        (
            interval_seconds,
            interval_seconds,
            etag or "",
            last_modified or "",
            feed_mode,
            channel_id,
        ),
    )
    await db.commit()


async def increment_rss_fail_streak(
    db: aiosqlite.Connection, channel_id: str, *, interval_seconds: int
) -> int:
    await db.execute(
        """
        UPDATE channels
        SET rss_fail_streak = rss_fail_streak + 1,
            rss_last_polled_at = datetime('now'),
            rss_poll_interval_seconds = ?,
            rss_next_poll_at = datetime('now', '+' || ? || ' seconds')
        WHERE channel_id = ?
        """,
        (interval_seconds, interval_seconds, channel_id),
    )
    await db.commit()
    cursor = await db.execute(
        "SELECT rss_fail_streak FROM channels WHERE channel_id = ?",
        (channel_id,),
    )
    row = await cursor.fetchone()
    return int(row["rss_fail_streak"]) if row else 0


async def touch_rss_last_polled_at(
    db: aiosqlite.Connection, channel_id: str, *, interval_seconds: int
) -> None:
    await db.execute(
        """
        UPDATE channels
        SET rss_last_polled_at = datetime('now'),
            rss_poll_interval_seconds = ?,
            rss_next_poll_at = datetime('now', '+' || ? || ' seconds')
        WHERE channel_id = ?
        """,
        (interval_seconds, interval_seconds, channel_id),
    )
    await db.commit()


async def update_rss_cache(
    db: aiosqlite.Connection,
    channel_id: str,
    *,
    etag: str | None,
    last_modified: str | None,
    feed_mode: str,
) -> None:
    await db.execute(
        """
        UPDATE channels
        SET rss_last_etag = ?,
            rss_last_modified = ?,
            rss_cache_feed_mode = ?
        WHERE channel_id = ?
        """,
        (etag or "", last_modified or "", feed_mode, channel_id),
    )
    await db.commit()


async def get_seconds_until_next_rss_poll(db: aiosqlite.Connection) -> float | None:
    cursor = await db.execute(
        """
        SELECT
            MIN((julianday(rss_next_poll_at) - julianday('now')) * 86400.0) AS delay_seconds
        FROM channels
        WHERE is_active = 1
          AND deleted_at IS NULL
          AND rss_next_poll_at IS NOT NULL
          AND trim(rss_next_poll_at) != ''
        """
    )
    row = await cursor.fetchone()
    if row is None or row["delay_seconds"] is None:
        return None
    return max(0.0, float(row["delay_seconds"]))


async def update_rss_priority(
    db: aiosqlite.Connection, channel_id: str, priority: str
) -> dict[str, Any] | None:
    normalized = normalize_rss_priority(priority)
    await db.execute(
        f"""
        UPDATE channels
        SET rss_priority = ?,
            rss_next_poll_at = datetime('now'),
            {sync_dirty_set_clause()}
        WHERE channel_id = ?
          AND deleted_at IS NULL
        """,
        (normalized, channel_id),
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
            rss_priority,
            rss_poll_interval_seconds,
            rss_next_poll_at,
            rss_last_etag,
            rss_last_modified,
            rss_cache_feed_mode,
            created_at
        FROM channels
        WHERE channel_id = ?
          AND deleted_at IS NULL
        """,
        (channel_id,),
    )
    return _row_to_dict(await cursor.fetchone())


async def count_active_channels(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        "SELECT COUNT(*) FROM channels WHERE is_active = 1 AND deleted_at IS NULL"
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


def is_newer_published(candidate: str, watermark: str | None) -> bool:
    if watermark is None:
        return True

    try:
        candidate_dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        watermark_dt = datetime.fromisoformat(watermark.replace("Z", "+00:00"))
    except ValueError:
        return candidate > watermark
    return candidate_dt > watermark_dt
