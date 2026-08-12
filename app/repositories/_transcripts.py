from __future__ import annotations

import json
from typing import Any, cast

import aiosqlite

import app.repositories._settings as settings_repository
from app.pipeline_status import PIPELINE_STATUSES
from app.repositories._common import UPDATED_AT_SQL
from app.repositories._common import (
    normalize_error_message as _normalize_error_message,
)
from app.repositories._common import (
    rows_to_dicts as _rows_to_dicts,
)
from app.repositories._common import (
    with_thumbnail_url as _with_thumbnail_url,
)

TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY = "transcript_request_headers_overrides_json"
PIPELINE_STATUS_KEYS = PIPELINE_STATUSES

get_settings_map = settings_repository.get_settings_map
get_setting = settings_repository.get_setting
set_setting = settings_repository.set_setting


async def list_queue_items(
    db: aiosqlite.Connection,
    statuses: tuple[str, ...],
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not statuses:
        return []
    safe_limit = max(1, min(500, int(limit)))
    placeholders = ", ".join("?" for _ in statuses)
    cursor = await db.execute(
        f"""
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.retry_count,
            v.transcript_retry_count,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE v.pipeline_status IN ({placeholders})
        ORDER BY
            CASE v.pipeline_status
                WHEN 'transcript_processing' THEN 0
                WHEN 'llm_processing' THEN 0
                WHEN 'transcript_pending' THEN 1
                WHEN 'llm_pending' THEN 1
                ELSE 2
            END,
            v.created_at ASC
        LIMIT ?
        """,
        (*statuses, safe_limit),
    )
    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def queue_status(db: aiosqlite.Connection) -> dict[str, int]:
    cursor = await db.execute(
        """
        SELECT
            pipeline_status,
            COUNT(1) AS cnt
        FROM videos
        GROUP BY pipeline_status
        """
    )
    rows = await cursor.fetchall()
    payload = {status: 0 for status in PIPELINE_STATUS_KEYS}
    payload["unknown_count"] = 0
    for row in rows:
        status = str(row["pipeline_status"] or "").strip()
        count = int(row["cnt"] or 0)
        if status in payload:
            payload[status] = count
        else:
            payload["unknown_count"] += count
    return payload


async def clear_transcript_queue_items(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'auto_paused',
            transcript_next_attempt_at = NULL
        WHERE pipeline_status IN ('transcript_pending', 'transcript_failed', 'no_subtitle')
        """
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def pop_pending_transcript_videos(
    db: aiosqlite.Connection,
    limit: int = 3,
    *,
    lookahead: int | None = None,
    avoid_channel_id: str | None = None,
) -> list[dict[str, Any]]:
    safe_limit = max(1, int(limit))
    safe_lookahead = max(safe_limit, int(lookahead or safe_limit))
    blocked_channel = str(avoid_channel_id or "").strip()
    cursor = await db.execute(
        """
        SELECT
            video_id,
            channel_id,
            title,
            upload_time,
            transcript_retry_count,
            transcript_next_attempt_at,
            transcript_target_language
        FROM videos
        WHERE pipeline_status = 'transcript_pending'
          AND (
              transcript_next_attempt_at IS NULL
              OR transcript_next_attempt_at <= datetime('now')
          )
        ORDER BY upload_time DESC, created_at DESC
        LIMIT ?
        """,
        (safe_lookahead,),
    )
    rows = await cursor.fetchall()
    candidates = _rows_to_dicts(rows)
    if not blocked_channel:
        return candidates[:safe_limit]

    preferred = [row for row in candidates if str(row.get("channel_id") or "") != blocked_channel]
    if len(preferred) >= safe_limit:
        return preferred[:safe_limit]

    return (preferred + candidates)[:safe_limit]


async def mark_transcript_processing(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_processing'
        WHERE video_id = ?
          AND pipeline_status = 'transcript_pending'
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def recover_stuck_transcript_jobs(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_pending'
        WHERE pipeline_status = 'transcript_processing'
        """
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def save_transcript(
    db: aiosqlite.Connection,
    video_id: str,
    raw_text: str,
    language: str | None,
    source_type: str,
    thumbnail_path: str | None,
    *,
    force_llm_pending: bool = False,
) -> None:
    await db.execute(
        f"""
        INSERT INTO transcripts(video_id, raw_text, language, source_type)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            raw_text=excluded.raw_text,
            language=excluded.language,
            source_type=excluded.source_type,
            updated_at={UPDATED_AT_SQL}
        """,
        (video_id, raw_text, language, source_type),
    )
    await db.execute(
        """
        UPDATE videos
        SET pipeline_status = CASE
                WHEN ? = 1 THEN 'llm_pending'
                WHEN processing_stage_snapshot = 'full' THEN 'llm_pending'
                ELSE 'transcript_done'
            END,
            processing_stage_snapshot = CASE lower(trim(coalesce(processing_stage_snapshot, '')))
                WHEN 'off' THEN 'off'
                WHEN 'transcript_only' THEN 'transcript_only'
                WHEN 'full' THEN 'full'
                ELSE 'full'
            END,
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_target_language = COALESCE(?, transcript_target_language),
            transcript_last_error = NULL,
            transcript_last_error_at = NULL,
            thumbnail_path = COALESCE(?, thumbnail_path)
        WHERE video_id = ?
        """,
        (1 if force_llm_pending else 0, language, thumbnail_path, video_id),
    )
    await db.commit()


async def mark_no_subtitle(db: aiosqlite.Connection, video_id: str) -> None:
    await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'no_subtitle',
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_last_error = NULL,
            transcript_last_error_at = NULL
        WHERE video_id = ?
        """,
        (video_id,),
    )
    await db.commit()


async def schedule_transcript_retry(
    db: aiosqlite.Connection,
    video_id: str,
    delay_seconds: int,
    error_message: str | None = None,
) -> int:
    safe_delay = max(1, int(delay_seconds))
    safe_error = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_pending',
            transcript_retry_count = transcript_retry_count + 1,
            transcript_next_attempt_at = datetime('now', ?),
            transcript_last_error = ?,
            transcript_last_error_at = datetime('now')
        WHERE video_id = ?
          AND pipeline_status IN ('transcript_pending', 'transcript_processing', 'transcript_failed')
        """,
        (f"+{safe_delay} seconds", safe_error, video_id),
    )
    await db.commit()
    return cursor.rowcount


async def defer_channel_transcript_retries(
    db: aiosqlite.Connection,
    channel_id: str,
    delay_seconds: int,
    exclude_video_id: str | None = None,
) -> int:
    safe_channel = str(channel_id).strip()
    if not safe_channel:
        return 0
    safe_delay = max(1, int(delay_seconds))
    safe_excluded = str(exclude_video_id or "").strip()
    cursor = await db.execute(
        """
        UPDATE videos
        SET transcript_next_attempt_at = CASE
            WHEN transcript_next_attempt_at IS NULL
                 OR transcript_next_attempt_at < datetime('now', ?)
            THEN datetime('now', ?)
            ELSE transcript_next_attempt_at
        END
        WHERE pipeline_status IN ('transcript_pending', 'transcript_processing')
          AND channel_id = ?
          AND (? = '' OR video_id != ?)
        """,
        (
            f"+{safe_delay} seconds",
            f"+{safe_delay} seconds",
            safe_channel,
            safe_excluded,
            safe_excluded,
        ),
    )
    await db.commit()
    return cursor.rowcount


async def mark_transcript_failed(
    db: aiosqlite.Connection,
    video_id: str,
    retry_count: int,
    error_message: str | None = None,
) -> int:
    safe_retry = max(0, int(retry_count))
    safe_error = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_failed',
            transcript_retry_count = ?,
            transcript_next_attempt_at = NULL,
            transcript_last_error = ?,
            transcript_last_error_at = datetime('now')
        WHERE video_id = ?
          AND pipeline_status IN ('transcript_pending', 'transcript_processing', 'transcript_failed', 'no_subtitle')
        """,
        (safe_retry, safe_error, video_id),
    )
    await db.commit()
    return cursor.rowcount


async def reset_transcript_for_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_pending',
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_last_error = NULL,
            transcript_last_error_at = NULL
        WHERE video_id = ?
          AND pipeline_status IN ('transcript_failed', 'no_subtitle')
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def reset_failed_transcripts_for_retry(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_pending',
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_last_error = NULL,
            transcript_last_error_at = NULL
        WHERE pipeline_status IN ('transcript_failed', 'no_subtitle')
        """
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def get_transcript_request_header_overrides(db: aiosqlite.Connection) -> dict[str, str]:
    raw = await get_setting(
        db,
        key=TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY,
        default="{}",
    )
    text = str(raw or "").strip() or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    parsed_dict = cast(dict[Any, Any], parsed)
    overrides: dict[str, str] = {}
    for key, value in parsed_dict.items():
        overrides[str(key).strip()] = str(value).strip()
    return overrides


async def save_transcript_request_header_overrides(
    db: aiosqlite.Connection,
    overrides: dict[str, str],
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in overrides.items():
        normalized[str(key).strip()] = str(value).strip()
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    await set_setting(
        db,
        key=TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY,
        value=payload,
    )
    return await get_transcript_request_header_overrides(db)
