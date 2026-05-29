from __future__ import annotations

from datetime import datetime
from typing import Any

import aiosqlite

from app.repositories._channels import (
    _row_to_dict,  # pyright: ignore[reportPrivateUsage]
)


async def pick_next_rss_channel(db: aiosqlite.Connection) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            rss_fail_streak,
            rss_last_polled_at,
            created_at
        FROM channels
        WHERE is_active = 1
        ORDER BY
            CASE WHEN rss_last_polled_at IS NULL THEN 0 ELSE 1 END,
            rss_last_polled_at ASC,
            created_at ASC
        LIMIT 1
        """,
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def mark_rss_poll_success(db: aiosqlite.Connection, channel_id: str) -> None:
    await db.execute(
        "UPDATE channels SET rss_fail_streak = 0, rss_last_polled_at = datetime('now') WHERE channel_id = ?",
        (channel_id,),
    )
    await db.commit()


async def increment_rss_fail_streak(db: aiosqlite.Connection, channel_id: str) -> int:
    await db.execute(
        "UPDATE channels SET rss_fail_streak = rss_fail_streak + 1, rss_last_polled_at = datetime('now') WHERE channel_id = ?",
        (channel_id,),
    )
    await db.commit()
    cursor = await db.execute(
        "SELECT rss_fail_streak FROM channels WHERE channel_id = ?",
        (channel_id,),
    )
    row = await cursor.fetchone()
    return int(row["rss_fail_streak"]) if row else 0


async def touch_rss_last_polled_at(db: aiosqlite.Connection, channel_id: str) -> None:
    await db.execute(
        "UPDATE channels SET rss_last_polled_at = datetime('now') WHERE channel_id = ?",
        (channel_id,),
    )
    await db.commit()


async def count_active_channels(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT COUNT(*) FROM channels WHERE is_active = 1")
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
