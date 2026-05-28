from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite

from app.repositories import (
    _alerts_retention as alerts_repository,
)
from app.repositories import (
    _settings as settings_repository,
)

LLM_QUEUE_STATUSES = ("llm_pending", "llm_processing", "llm_failed", "manual_review")
LLM_ARTICLE_PROVIDER_UNKNOWN = "unknown"
LLM_CONFIG_MISSING_ALERT_SENT_KEY = settings_repository.LLM_CONFIG_MISSING_ALERT_SENT_KEY
LLM_SCHEMA_INVALID_ALERT_SENT_KEY = settings_repository.LLM_SCHEMA_INVALID_ALERT_SENT_KEY
LLM_RUNTIME_LAST_CODE_KEY = settings_repository.LLM_RUNTIME_LAST_CODE_KEY
LLM_RUNTIME_LAST_MESSAGE_KEY = settings_repository.LLM_RUNTIME_LAST_MESSAGE_KEY
LLM_RUNTIME_LAST_SEEN_AT_KEY = settings_repository.LLM_RUNTIME_LAST_SEEN_AT_KEY

get_setting = settings_repository.get_setting
set_setting = settings_repository.set_setting


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _normalize_article_provider(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in settings_repository.LLM_PROVIDER_OPTIONS:
        return normalized
    return LLM_ARTICLE_PROVIDER_UNKNOWN


def _normalize_article_text_meta(value: str | None, *, max_length: int = 200) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length]


async def clear_llm_queue_items(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_done'
        WHERE pipeline_status IN ('llm_pending', 'llm_failed', 'manual_review')
        """
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def repair_orphan_llm_candidates(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'manual_review'
        WHERE pipeline_status IN ('llm_pending', 'llm_failed', 'llm_processing')
          AND NOT EXISTS (
              SELECT 1
              FROM transcripts t
              WHERE t.video_id = videos.video_id
          )
        """
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def pop_llm_candidate(
    db: aiosqlite.Connection, max_retry_count: int
) -> dict[str, Any] | None:
    safe_max_retry_count = max(1, int(max_retry_count))
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.title,
            v.retry_count,
            t.raw_text
        FROM videos v
        JOIN transcripts t ON t.video_id = v.video_id
        WHERE v.pipeline_status IN ('llm_pending', 'llm_failed')
          AND v.retry_count < ?
        ORDER BY v.created_at ASC
        LIMIT 1
        """,
        (safe_max_retry_count,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def mark_restructure_processing(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_processing'
        WHERE video_id = ?
          AND pipeline_status IN ('llm_pending', 'llm_failed')
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def save_article(
    db: aiosqlite.Connection,
    video_id: str,
    title: str,
    lead: str,
    body: str,
    fact_box: str | None,
    timestamps: str | None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_reasoning_effort: str | None = None,
    llm_generated_at: str | None = None,
) -> int:
    safe_provider = _normalize_article_provider(llm_provider)
    safe_model = _normalize_article_text_meta(llm_model)
    safe_reasoning_effort = _normalize_article_text_meta(llm_reasoning_effort, max_length=32)
    safe_generated_at = str(llm_generated_at or "").strip() or None

    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'done'
        WHERE video_id = ?
          AND pipeline_status = 'llm_processing'
        """,
        (video_id,),
    )
    affected = int(cursor.rowcount or 0)
    if affected == 0:
        await db.commit()
        return 0

    try:
        await db.execute(
            """
            INSERT INTO articles(
                video_id,
                title,
                lead,
                body,
                fact_box,
                timestamps,
                llm_provider,
                llm_model,
                llm_reasoning_effort,
                llm_generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
            ON CONFLICT(video_id) DO UPDATE SET
                title=excluded.title,
                lead=excluded.lead,
                body=excluded.body,
                fact_box=excluded.fact_box,
                timestamps=excluded.timestamps,
                llm_provider=excluded.llm_provider,
                llm_model=excluded.llm_model,
                llm_reasoning_effort=excluded.llm_reasoning_effort,
                llm_generated_at=excluded.llm_generated_at
            """,
            (
                video_id,
                title,
                lead,
                body,
                fact_box,
                timestamps,
                safe_provider,
                safe_model,
                safe_reasoning_effort,
                safe_generated_at,
            ),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return affected


async def mark_restructure_failed(
    db: aiosqlite.Connection,
    video_id: str,
    retry_count: int,
    max_retry_count: int,
) -> tuple[str, int]:
    safe_max_retry_count = max(1, int(max_retry_count))
    next_retry = retry_count + 1
    next_status = "llm_failed" if next_retry < safe_max_retry_count else "manual_review"
    cursor = await db.execute(
        """
        UPDATE videos
        SET retry_count = ?, pipeline_status = ?
        WHERE video_id = ?
          AND pipeline_status = 'llm_processing'
        """,
        (next_retry, next_status, video_id),
    )
    await db.commit()
    return next_status, int(cursor.rowcount or 0)


async def requeue_llm_pending_without_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending'
        WHERE video_id = ?
          AND pipeline_status = 'llm_processing'
        """,
        (video_id,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def ensure_llm_config_missing_alert(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos
        WHERE pipeline_status IN ('llm_pending', 'llm_failed')
        """
    )
    row = await cursor.fetchone()
    pending_count = int((row["cnt"] if row is not None else 0) or 0)
    if pending_count <= 0:
        sent_raw = await get_setting(
            db,
            key=LLM_CONFIG_MISSING_ALERT_SENT_KEY,
            default="0",
        )
        if str(sent_raw or "0").strip() != "0":
            await set_setting(db, key=LLM_CONFIG_MISSING_ALERT_SENT_KEY, value="0")
        return False

    sent_raw = await get_setting(
        db,
        key=LLM_CONFIG_MISSING_ALERT_SENT_KEY,
        default="0",
    )
    if str(sent_raw or "0").strip() == "1":
        return False

    await alerts_repository.create_system_alert(
        db,
        alert_type=alerts_repository.ALERT_TYPE_LLM_CONFIG_MISSING,
        message="LLM runtime is not ready. llm_pending items are waiting.",
    )
    await set_setting(db, key=LLM_CONFIG_MISSING_ALERT_SENT_KEY, value="1")
    return True


async def clear_llm_config_missing_alert_flag(db: aiosqlite.Connection) -> None:
    sent_raw = await get_setting(
        db,
        key=LLM_CONFIG_MISSING_ALERT_SENT_KEY,
        default="0",
    )
    if str(sent_raw or "0").strip() == "0":
        return
    await set_setting(db, key=LLM_CONFIG_MISSING_ALERT_SENT_KEY, value="0")


async def ensure_llm_schema_invalid_alert(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos
        WHERE pipeline_status IN ('llm_pending', 'llm_failed')
        """
    )
    row = await cursor.fetchone()
    pending_count = int((row["cnt"] if row is not None else 0) or 0)
    if pending_count <= 0:
        sent_raw = await get_setting(
            db,
            key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY,
            default="0",
        )
        if str(sent_raw or "0").strip() != "0":
            await set_setting(db, key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY, value="0")
        return False

    sent_raw = await get_setting(
        db,
        key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY,
        default="0",
    )
    if str(sent_raw or "0").strip() == "1":
        return False

    cursor = await db.execute(
        """
        SELECT 1
        FROM system_alerts
        WHERE alert_type = ?
          AND acknowledged_at IS NULL
        LIMIT 1
        """,
        (alerts_repository.ALERT_TYPE_LLM_SCHEMA_INVALID,),
    )
    existing = await cursor.fetchone()
    if existing is not None:
        await set_setting(db, key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY, value="1")
        return False

    await alerts_repository.create_system_alert(
        db,
        alert_type=alerts_repository.ALERT_TYPE_LLM_SCHEMA_INVALID,
        message="LLM output schema is incompatible. llm_pending items are waiting.",
    )
    await set_setting(db, key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY, value="1")
    return True


async def clear_llm_schema_invalid_alert_flag(db: aiosqlite.Connection) -> None:
    sent_raw = await get_setting(
        db,
        key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY,
        default="0",
    )
    if str(sent_raw or "0").strip() == "0":
        return
    await set_setting(db, key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY, value="0")


async def set_llm_runtime_issue(
    db: aiosqlite.Connection,
    *,
    code: str,
    message: str,
    seen_at: str | None = None,
) -> None:
    normalized_code = str(code or "").strip()
    normalized_message = str(message or "").strip()
    normalized_seen_at = str(seen_at or "").strip()
    if not normalized_seen_at:
        normalized_seen_at = datetime.now(UTC).isoformat()
    await set_setting(db, key=LLM_RUNTIME_LAST_CODE_KEY, value=normalized_code)
    await set_setting(db, key=LLM_RUNTIME_LAST_MESSAGE_KEY, value=normalized_message)
    await set_setting(db, key=LLM_RUNTIME_LAST_SEEN_AT_KEY, value=normalized_seen_at)


async def clear_llm_runtime_issue(db: aiosqlite.Connection) -> None:
    await set_setting(db, key=LLM_RUNTIME_LAST_CODE_KEY, value="")
    await set_setting(db, key=LLM_RUNTIME_LAST_MESSAGE_KEY, value="")
    await set_setting(db, key=LLM_RUNTIME_LAST_SEEN_AT_KEY, value="")


async def get_llm_runtime_issue(db: aiosqlite.Connection) -> dict[str, str]:
    code = str(await get_setting(db, key=LLM_RUNTIME_LAST_CODE_KEY, default="") or "").strip()
    message = str(await get_setting(db, key=LLM_RUNTIME_LAST_MESSAGE_KEY, default="") or "").strip()
    seen_at = str(await get_setting(db, key=LLM_RUNTIME_LAST_SEEN_AT_KEY, default="") or "").strip()
    return {
        "code": code,
        "message": message,
        "seen_at": seen_at,
    }


async def count_llm_pending_videos(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos v
        WHERE v.pipeline_status IN ('llm_pending', 'llm_failed')
        """
    )
    row = await cursor.fetchone()
    return int((row["cnt"] if row is not None else 0) or 0)
