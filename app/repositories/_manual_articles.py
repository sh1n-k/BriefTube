from __future__ import annotations

import logging
import sqlite3
from typing import Any

import aiosqlite

from app.pipeline_status import (
    MANUAL_ARTICLE_ENQUEUE_RETRY_PIPELINE_STATUSES,
    MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES,
)
from app.repositories._common import (
    normalize_error_message as _normalize_error_message,
)
from app.repositories._common import (
    row_to_dict as _row_to_dict,
)

logger = logging.getLogger(__name__)


async def get_manual_article_job(
    db: aiosqlite.Connection,
    job_id: int,
) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            j.id,
            j.video_id,
            j.status,
            j.error_message,
            j.requested_at,
            j.started_at,
            j.finished_at,
            j.updated_at,
            v.channel_id,
            v.pipeline_status,
            v.transcript_retry_count,
            v.transcript_target_language,
            EXISTS(
                SELECT 1
                FROM transcripts t
                WHERE t.video_id = j.video_id
                  AND t.deleted_at IS NULL
            ) AS has_transcript
        FROM manual_article_jobs j
        LEFT JOIN videos v ON v.video_id = j.video_id AND v.deleted_at IS NULL
        WHERE j.id = ?
        """,
        (int(job_id),),
    )
    row = await cursor.fetchone()
    payload = _row_to_dict(row)
    if payload is None:
        return None
    payload["has_transcript"] = bool(payload.get("has_transcript"))
    return payload


async def enqueue_manual_article_jobs(
    db: aiosqlite.Connection,
    video_ids: list[str],
) -> dict[str, Any]:
    summary: dict[str, list[str]] = {
        "new": [],
        "retry": [],
        "skip": [],
        "failed": [],
    }
    requested_ids = list(video_ids or [])
    normalized_ids: list[str] = []
    seen_ids: set[str] = set()

    for raw_video_id in requested_ids:
        normalized = str(raw_video_id or "").strip()
        if not normalized:
            summary["failed"].append("")
            continue
        if normalized in seen_ids:
            summary["skip"].append(normalized)
            continue
        seen_ids.add(normalized)
        normalized_ids.append(normalized)

    if not normalized_ids:
        payload = {
            "new": summary["new"],
            "retry": summary["retry"],
            "skip": summary["skip"],
            "failed": summary["failed"],
            "new_count": len(summary["new"]),
            "retry_count": len(summary["retry"]),
            "skip_count": len(summary["skip"]),
            "failed_count": len(summary["failed"]),
        }
        logger.info(
            "event=manual_article.enqueue requested=%s new=%s retry=%s skip=%s failed=%s",
            len(requested_ids),
            payload["new_count"],
            payload["retry_count"],
            payload["skip_count"],
            payload["failed_count"],
            extra={"event": "manual_article.enqueue"},
        )
        return payload

    placeholders = ",".join(["?"] * len(normalized_ids))
    videos_cursor = await db.execute(
        f"""
        SELECT
            video_id,
            pipeline_status
        FROM videos
        WHERE video_id IN ({placeholders})
          AND deleted_at IS NULL
        """,
        tuple(normalized_ids),
    )
    video_rows = await videos_cursor.fetchall()
    video_status_map = {
        str(row["video_id"]): str(row["pipeline_status"] or "").strip().lower()
        for row in video_rows
    }

    active_cursor = await db.execute(
        f"""
        SELECT DISTINCT video_id
        FROM manual_article_jobs
        WHERE video_id IN ({placeholders})
          AND status IN ('pending', 'running')
        """,
        tuple(normalized_ids),
    )
    active_rows = await active_cursor.fetchall()
    active_video_ids = {str(row["video_id"]) for row in active_rows}

    created_count = 0
    for video_id in normalized_ids:
        pipeline_status = video_status_map.get(video_id)
        if pipeline_status is None:
            summary["failed"].append(video_id)
            continue

        if video_id in active_video_ids:
            summary["skip"].append(video_id)
            continue

        if pipeline_status in MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES:
            summary["skip"].append(video_id)
            continue

        category = (
            "retry" if pipeline_status in MANUAL_ARTICLE_ENQUEUE_RETRY_PIPELINE_STATUSES else "new"
        )
        try:
            await db.execute(
                """
                INSERT INTO manual_article_jobs(
                    video_id,
                    status,
                    requested_at,
                    updated_at
                )
                VALUES (?, 'pending', datetime('now'), datetime('now'))
                """,
                (video_id,),
            )
            summary[category].append(video_id)
            created_count += 1
        except sqlite3.IntegrityError:
            summary["skip"].append(video_id)

    if created_count > 0:
        await db.commit()

    payload = {
        "new": summary["new"],
        "retry": summary["retry"],
        "skip": summary["skip"],
        "failed": summary["failed"],
        "new_count": len(summary["new"]),
        "retry_count": len(summary["retry"]),
        "skip_count": len(summary["skip"]),
        "failed_count": len(summary["failed"]),
    }
    logger.info(
        "event=manual_article.enqueue requested=%s new=%s retry=%s skip=%s failed=%s",
        len(requested_ids),
        payload["new_count"],
        payload["retry_count"],
        payload["skip_count"],
        payload["failed_count"],
        extra={"event": "manual_article.enqueue"},
    )
    return payload


async def claim_next_manual_article_job(db: aiosqlite.Connection) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT id
        FROM manual_article_jobs
        WHERE status = 'pending'
        ORDER BY requested_at ASC, id ASC
        LIMIT 1
        """
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    job_id = int(row["id"])
    updated = await db.execute(
        """
        UPDATE manual_article_jobs
        SET
            status = 'running',
            started_at = COALESCE(started_at, datetime('now')),
            finished_at = NULL,
            error_message = NULL,
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'pending'
        """,
        (job_id,),
    )
    if int(updated.rowcount or 0) == 0:
        return None

    await db.commit()
    claimed = await get_manual_article_job(db, job_id)
    if claimed is not None:
        logger.info(
            "event=manual_article.claim job_id=%s video_id=%s",
            claimed["id"],
            claimed["video_id"],
            extra={"event": "manual_article.claim"},
        )
    return claimed


async def mark_manual_article_job_succeeded(db: aiosqlite.Connection, *, job_id: int) -> int:
    cursor = await db.execute(
        """
        UPDATE manual_article_jobs
        SET
            status = 'succeeded',
            error_message = NULL,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (int(job_id),),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_manual_article_job_failed(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    error_message: str,
) -> int:
    normalized_error = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE manual_article_jobs
        SET
            status = 'failed',
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (normalized_error, int(job_id)),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_manual_article_job_skipped(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    reason: str | None = None,
) -> int:
    normalized_reason = _normalize_error_message(reason)
    cursor = await db.execute(
        """
        UPDATE manual_article_jobs
        SET
            status = 'skipped',
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (normalized_reason or None, int(job_id)),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def recover_stuck_manual_article_jobs(
    db: aiosqlite.Connection,
    *,
    stale_after_seconds: int | None = None,
    exclude_job_ids: list[int] | None = None,
) -> int:
    where_clauses: list[str] = ["status = 'running'"]
    query_params: list[Any] = []
    recovery_mode = "startup"
    error_message = "manual article worker interrupted (app restart/shutdown)"

    safe_stale_after_seconds: int | None = None
    if stale_after_seconds is not None:
        safe_stale_after_seconds = max(1, int(stale_after_seconds))
        threshold = f"-{safe_stale_after_seconds} seconds"
        where_clauses.append("COALESCE(started_at, updated_at, requested_at) <= datetime('now', ?)")
        where_clauses.append("updated_at <= datetime('now', ?)")
        query_params.extend([threshold, threshold])
        recovery_mode = "runtime"
        error_message = (
            f"manual article worker stale timeout exceeded ({safe_stale_after_seconds}s)"
        )

    normalized_exclude_ids: list[int] = []
    for raw_job_id in exclude_job_ids or []:
        try:
            parsed_id = int(raw_job_id)
        except (TypeError, ValueError):
            continue
        if parsed_id > 0:
            normalized_exclude_ids.append(parsed_id)
    normalized_exclude_ids = sorted(set(normalized_exclude_ids))
    if normalized_exclude_ids:
        placeholders = ",".join(["?"] * len(normalized_exclude_ids))
        where_clauses.append(f"id NOT IN ({placeholders})")
        query_params.extend(normalized_exclude_ids)

    cursor = await db.execute(
        f"""
        UPDATE manual_article_jobs
        SET
            status = 'failed',
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE {" AND ".join(where_clauses)}
        """,
        (error_message, *query_params),
    )
    await db.commit()
    recovered = int(cursor.rowcount or 0)
    if recovered > 0:
        logger.info(
            "event=manual_article.recover mode=%s recovered=%s stale_after_seconds=%s",
            recovery_mode,
            recovered,
            safe_stale_after_seconds if safe_stale_after_seconds is not None else "-",
            extra={"event": "manual_article.recover"},
        )
    return recovered


async def ensure_video_llm_pending_for_manual_article(
    db: aiosqlite.Connection, video_id: str
) -> int:
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return 0
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending',
            retry_count = 0
        WHERE video_id = ?
          AND pipeline_status NOT IN ('llm_pending', 'llm_processing', 'done')
          AND deleted_at IS NULL
        """,
        (normalized_video_id,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def force_mark_video_transcript_failed_for_manual_article(
    db: aiosqlite.Connection,
    *,
    video_id: str,
    retry_count: int,
    error_message: str,
) -> int:
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return 0
    safe_retry_count = max(1, int(retry_count))
    safe_error_message = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_failed',
            transcript_retry_count = ?,
            transcript_next_attempt_at = NULL,
            transcript_last_error = ?,
            transcript_last_error_at = datetime('now')
        WHERE video_id = ?
          AND deleted_at IS NULL
        """,
        (safe_retry_count, safe_error_message, normalized_video_id),
    )
    await db.commit()
    return int(cursor.rowcount or 0)
