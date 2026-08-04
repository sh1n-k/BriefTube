from __future__ import annotations

import logging
import sqlite3
from typing import Any

import aiosqlite

from app.pipeline_status import MANUAL_TRANSCRIPT_ALLOWED_PIPELINE_STATUSES
from app.repositories._common import (
    normalize_error_message as _normalize_error_message,
)
from app.repositories._common import (
    row_to_dict as _row_to_dict,
)

logger = logging.getLogger(__name__)


async def get_manual_transcript_job(
    db: aiosqlite.Connection,
    job_id: int,
) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            j.id,
            j.video_id,
            j.status,
            j.retry_count,
            j.next_attempt_at,
            j.error_message,
            j.language,
            j.source_type,
            j.requested_at,
            j.started_at,
            j.finished_at,
            j.updated_at,
            v.channel_id,
            v.title,
            v.pipeline_status,
            v.transcript_retry_count,
            v.transcript_target_language,
            EXISTS(
                SELECT 1
                FROM transcripts t
                WHERE t.video_id = j.video_id
            ) AS has_transcript
        FROM manual_transcript_jobs j
        LEFT JOIN videos v ON v.video_id = j.video_id
        WHERE j.id = ?
        """,
        (int(job_id),),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_active_manual_transcript_job_for_video(
    db: aiosqlite.Connection,
    video_id: str,
) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT id
        FROM manual_transcript_jobs
        WHERE video_id = ?
          AND status IN ('pending', 'running')
        ORDER BY requested_at DESC, id DESC
        LIMIT 1
        """,
        (str(video_id).strip(),),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return await get_manual_transcript_job(db, int(row["id"]))


async def enqueue_manual_transcript_job(
    db: aiosqlite.Connection,
    video_id: str,
) -> dict[str, Any]:
    normalized_video_id = str(video_id or "").strip()
    if not normalized_video_id:
        return {"status": "failed", "reason": "invalid_video_id", "job_id": None}

    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.pipeline_status,
            EXISTS(
                SELECT 1
                FROM transcripts t
                WHERE t.video_id = v.video_id
            ) AS has_transcript
        FROM videos v
        WHERE v.video_id = ?
        """,
        (normalized_video_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {"status": "failed", "reason": "not_found", "job_id": None}

    pipeline_status = str(row["pipeline_status"] or "").strip().lower()
    has_transcript = bool(row["has_transcript"])
    if has_transcript:
        return {"status": "skipped", "reason": "has_transcript", "job_id": None}
    if pipeline_status not in MANUAL_TRANSCRIPT_ALLOWED_PIPELINE_STATUSES:
        return {
            "status": "failed",
            "reason": f"pipeline_status:{pipeline_status}",
            "job_id": None,
        }

    active = await get_active_manual_transcript_job_for_video(db, normalized_video_id)
    if active is not None:
        return {
            "status": "skipped",
            "reason": "active_job_exists",
            "job_id": int(active["id"]),
        }

    try:
        insert = await db.execute(
            """
            INSERT INTO manual_transcript_jobs(
                video_id,
                status,
                requested_at,
                updated_at
            )
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            (normalized_video_id,),
        )
    except sqlite3.IntegrityError:
        active_after_race = await get_active_manual_transcript_job_for_video(
            db, normalized_video_id
        )
        return {
            "status": "skipped",
            "reason": "active_job_exists",
            "job_id": int(active_after_race["id"]) if active_after_race else None,
        }

    await db.commit()
    job_id = int(insert.lastrowid or 0)
    logger.info(
        "event=manual_transcript.enqueue video_id=%s job_id=%s",
        normalized_video_id,
        job_id,
        extra={"event": "manual_transcript.enqueue"},
    )
    return {"status": "queued", "reason": "", "job_id": job_id}


async def claim_next_manual_transcript_job(db: aiosqlite.Connection) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT id
        FROM manual_transcript_jobs
        WHERE status = 'pending'
          AND (
              next_attempt_at IS NULL
              OR next_attempt_at <= datetime('now')
          )
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
        UPDATE manual_transcript_jobs
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
    claimed = await get_manual_transcript_job(db, job_id)
    if claimed is not None:
        logger.info(
            "event=manual_transcript.claim job_id=%s video_id=%s",
            claimed["id"],
            claimed["video_id"],
            extra={"event": "manual_transcript.claim"},
        )
    return claimed


async def mark_manual_transcript_job_succeeded(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    language: str | None,
    source_type: str | None,
) -> int:
    cursor = await db.execute(
        """
        UPDATE manual_transcript_jobs
        SET
            status = 'succeeded',
            error_message = NULL,
            language = ?,
            source_type = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (language, source_type, int(job_id)),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_manual_transcript_job_failed(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    error_message: str,
    retry_count: int | None = None,
) -> int:
    normalized_error = _normalize_error_message(error_message)
    if retry_count is None:
        cursor = await db.execute(
            """
            UPDATE manual_transcript_jobs
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
    else:
        cursor = await db.execute(
            """
            UPDATE manual_transcript_jobs
            SET
                status = 'failed',
                retry_count = ?,
                error_message = ?,
                finished_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
              AND status = 'running'
            """,
            (max(0, int(retry_count)), normalized_error, int(job_id)),
        )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_manual_transcript_job_skipped(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    reason: str | None = None,
) -> int:
    normalized_reason = _normalize_error_message(reason)
    cursor = await db.execute(
        """
        UPDATE manual_transcript_jobs
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


async def recover_stuck_manual_transcript_jobs(
    db: aiosqlite.Connection,
    *,
    stale_after_seconds: int | None = None,
    exclude_job_ids: list[int] | None = None,
) -> int:
    error_message = "manual transcript worker interrupted (app restart/shutdown)"
    query_params: list[Any] = [error_message]
    where_clauses = ["status = 'running'"]
    safe_stale_after_seconds: int | None = None
    if stale_after_seconds is not None:
        safe_stale_after_seconds = max(1, int(stale_after_seconds))
        threshold = f"-{safe_stale_after_seconds} seconds"
        where_clauses.append("COALESCE(started_at, updated_at, requested_at) <= datetime('now', ?)")
        where_clauses.append("updated_at <= datetime('now', ?)")
        query_params.extend([threshold, threshold])
        query_params[0] = (
            f"manual transcript worker stale timeout exceeded ({safe_stale_after_seconds}s)"
        )

    normalized_exclude_ids = sorted(
        {
            int(job_id)
            for job_id in (exclude_job_ids or [])
            if str(job_id).strip().isdigit() and int(job_id) > 0
        }
    )
    if normalized_exclude_ids:
        placeholders = ",".join(["?"] * len(normalized_exclude_ids))
        where_clauses.append(f"id NOT IN ({placeholders})")
        query_params.extend(normalized_exclude_ids)
    cursor = await db.execute(
        f"""
        UPDATE manual_transcript_jobs
        SET
            status = 'failed',
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE {" AND ".join(where_clauses)}
        """,
        tuple(query_params),
    )
    await db.commit()
    recovered = int(cursor.rowcount or 0)
    if recovered > 0:
        logger.warning(
            "event=manual_transcript.recover recovered=%s stale_after_seconds=%s",
            recovered,
            safe_stale_after_seconds if safe_stale_after_seconds is not None else "-",
            extra={"event": "manual_transcript.recover"},
        )
    return recovered
