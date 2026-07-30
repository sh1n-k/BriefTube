from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

import aiosqlite

from app.repositories._common import (
    rows_to_dicts as _rows_to_dicts,
)
from app.repositories._common import (
    with_thumbnail_url as _with_thumbnail_url,
)

ALERT_TYPE_RSS_CHANNEL_NOT_FOUND = "rss_channel_not_found"
ALERT_TYPE_LLM_CONFIG_MISSING = "llm_config_missing"
ALERT_TYPE_LLM_SCHEMA_INVALID = "llm_schema_invalid"
ALERT_TYPE_TELEGRAM_SEND_FAILED = "telegram_send_failed"
RETENTION_MATCH_BATCH_SIZE = 500


async def create_system_alert(
    db: aiosqlite.Connection,
    alert_type: str,
    message: str,
    channel_id: str | None = None,
    channel_name: str | None = None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO system_alerts(alert_type, channel_id, channel_name, message)
        VALUES (?, ?, ?, ?)
        """,
        (alert_type, channel_id, channel_name, message),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def list_unacknowledged_alert_groups(
    db: aiosqlite.Connection,
    limit: int = 5,
) -> list[dict[str, Any]]:
    safe_limit = max(1, limit)
    cursor = await db.execute(
        """
        WITH top_alert_types AS (
            SELECT
                alert_type,
                MAX(created_at) AS latest_created_at
            FROM system_alerts
            WHERE acknowledged_at IS NULL
            GROUP BY alert_type
            ORDER BY latest_created_at DESC, alert_type DESC
            LIMIT ?
        )
        SELECT
            s.id,
            s.alert_type,
            s.channel_id,
            s.channel_name,
            s.message,
            s.created_at,
            t.latest_created_at
        FROM system_alerts s
        JOIN top_alert_types t
          ON t.alert_type = s.alert_type
        WHERE s.acknowledged_at IS NULL
        ORDER BY t.latest_created_at DESC, s.alert_type DESC, s.created_at DESC, s.id DESC
        """,
        (safe_limit,),
    )
    rows = await cursor.fetchall()
    alerts = _rows_to_dicts(rows)

    grouped: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        alert_type = str(alert.get("alert_type") or "").strip() or "unknown"
        group = grouped.get(alert_type)
        if group is None:
            members: list[dict[str, Any]] = []
            group = {
                "alert_type": alert_type,
                "count": 0,
                "latest_created_at": str(
                    alert.get("latest_created_at") or alert.get("created_at") or ""
                ),
                "members": members,
            }
            grouped[alert_type] = group
        else:
            members = cast(list[dict[str, Any]], group["members"])

        group["count"] = cast(int, group["count"]) + 1
        members.append(
            {
                "id": alert["id"],
                "channel_id": alert["channel_id"],
                "channel_name": alert["channel_name"],
                "message": alert["message"],
                "created_at": alert["created_at"],
            }
        )

    groups = list(grouped.values())
    groups.sort(
        key=lambda item: (
            str(item.get("latest_created_at") or ""),
            str(item.get("alert_type") or ""),
        ),
        reverse=True,
    )
    return groups[:safe_limit]


async def acknowledge_alert(db: aiosqlite.Connection, alert_id: int) -> int:
    cursor = await db.execute(
        """
        UPDATE system_alerts
        SET acknowledged_at = datetime('now')
        WHERE id = ?
          AND acknowledged_at IS NULL
        """,
        (alert_id,),
    )
    await db.commit()
    return cursor.rowcount


async def acknowledge_alerts_by_type(db: aiosqlite.Connection, alert_type: str) -> int:
    cursor = await db.execute(
        """
        UPDATE system_alerts
        SET acknowledged_at = datetime('now')
        WHERE alert_type = ?
          AND acknowledged_at IS NULL
        """,
        (alert_type,),
    )
    await db.commit()
    return cursor.rowcount


async def count_retention_expired_videos(db: aiosqlite.Connection, retention_days: int) -> int:
    modifier = f"-{max(1, int(retention_days))} days"
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos
        WHERE datetime(upload_time) <= datetime('now', ?)
          AND deleted_at IS NULL
        """,
        (modifier,),
    )
    row = await cursor.fetchone()
    return int((row["cnt"] if row else 0) or 0)


async def list_retention_expired_video_ids(
    db: aiosqlite.Connection,
    retention_days: int,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[str]:
    modifier = f"-{max(1, int(retention_days))} days"
    params: list[Any] = [modifier]
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
    cursor = await db.execute(
        f"""
        SELECT video_id
        FROM videos
        WHERE datetime(upload_time) <= datetime('now', ?)
          AND deleted_at IS NULL
        ORDER BY datetime(upload_time) ASC, created_at ASC
        {limit_clause}
        """,
        tuple(params),
    )
    rows = await cursor.fetchall()
    return [str(row["video_id"]) for row in rows]


async def list_retention_expired_matching_video_ids(
    db: aiosqlite.Connection,
    retention_days: int,
    video_ids: Iterable[str],
) -> list[str]:
    normalized = [
        str(video_id).strip() for video_id in dict.fromkeys(video_ids) if str(video_id).strip()
    ]
    if not normalized:
        return []

    modifier = f"-{max(1, int(retention_days))} days"
    expired: set[str] = set()
    for start in range(0, len(normalized), RETENTION_MATCH_BATCH_SIZE):
        batch = normalized[start : start + RETENTION_MATCH_BATCH_SIZE]
        placeholders = ",".join(["?"] * len(batch))
        cursor = await db.execute(
            f"""
            SELECT video_id
            FROM videos
            WHERE video_id IN ({placeholders})
              AND datetime(upload_time) <= datetime('now', ?)
              AND deleted_at IS NULL
            """,
            (*batch, modifier),
        )
        rows = await cursor.fetchall()
        expired.update(str(row["video_id"]) for row in rows)
    return [video_id for video_id in normalized if video_id in expired]


async def list_retention_expired_videos(
    db: aiosqlite.Connection,
    retention_days: int,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    modifier = f"-{max(1, int(retention_days))} days"
    params: list[Any] = [modifier]
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
    cursor = await db.execute(
        f"""
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE datetime(v.upload_time) <= datetime('now', ?)
          AND v.deleted_at IS NULL
        ORDER BY datetime(v.upload_time) ASC, v.created_at ASC
        {limit_clause}
        """,
        tuple(params),
    )
    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]
