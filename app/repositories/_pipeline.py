"""Video pipeline state: target stage, video_jobs transitions and derived status.

This module is the only writer of ``video_jobs`` and ``videos.target_stage``.
Facts (``transcripts``/``articles`` rows) and work (``video_jobs``) are kept apart;
the derived per-video status is read from the ``video_pipeline_v`` view.
"""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite

from app.database_migrations import PIPELINE_VIEW_NAME
from app.pipeline_status import (
    MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES,
    MANUAL_TRANSCRIPT_ALLOWED_PIPELINE_STATUSES,
    PIPELINE_STATUSES,
)
from app.repositories._common import UPDATED_AT_SQL
from app.repositories._common import (
    normalize_error_message as _normalize_error_message,
)
from app.repositories._common import (
    row_to_dict as _row_to_dict,
)
from app.repositories._common import (
    rows_to_dicts as _rows_to_dicts,
)
from app.repositories._common import (
    with_thumbnail_url as _with_thumbnail_url,
)

logger = logging.getLogger(__name__)

JOB_KIND_TRANSCRIPT = "transcript"
JOB_KIND_ARTICLE = "article"
JOB_KINDS: tuple[str, ...] = (JOB_KIND_TRANSCRIPT, JOB_KIND_ARTICLE)
JOB_ORIGIN_AUTO = "auto"
JOB_ORIGIN_MANUAL = "manual"
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_FAILED = "failed"

ERROR_CODE_NO_SUBTITLE = "no_subtitle"
ERROR_CODE_EMPTY_TRANSCRIPT = "empty_transcript"
ERROR_CODE_TRANSCRIPT_MISSING = "transcript_missing"
ERROR_CODE_RETRY_EXHAUSTED = "retry_exhausted"

# Category processing_stage → videos.target_stage at ingest.
TARGET_STAGE_FROM_CATEGORY_SQL = """
    CASE lower(trim(coalesce(cat.processing_stage, '')))
        WHEN 'off' THEN 'none'
        WHEN 'transcript_only' THEN 'transcript'
        ELSE 'article'
    END
"""

_LATEST_JOB_PREDICATE = """
    id = (
        SELECT max(j2.id) FROM video_jobs j2
        WHERE j2.video_id = video_jobs.video_id AND j2.kind = video_jobs.kind
    )
"""


def _normalize_error_code(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized[:64] or "unknown"


async def enqueue_initial_transcript_job(db: aiosqlite.Connection, video_id: str) -> int:
    """Queue the first transcript job of a newly inserted video. Caller commits."""
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO video_jobs(video_id, kind, origin, status)
        SELECT v.video_id, 'transcript', 'auto', 'queued'
        FROM videos v
        WHERE v.video_id = ?
          AND v.target_stage != 'none'
          AND NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.video_id)
        """,
        (video_id,),
    )
    return int(cursor.rowcount or 0)


# --- transcript jobs -------------------------------------------------------------


async def list_claimable_transcript_jobs(
    db: aiosqlite.Connection,
    limit: int = 3,
    *,
    lookahead: int | None = None,
    avoid_channel_id: str | None = None,
) -> list[dict[str, Any]]:
    """Due transcript jobs, manual requests first, then newest uploads."""
    safe_limit = max(1, int(limit))
    safe_lookahead = max(safe_limit, int(lookahead or safe_limit))
    blocked_channel = str(avoid_channel_id or "").strip()
    cursor = await db.execute(
        """
        SELECT
            j.id AS job_id,
            j.origin,
            j.attempt_count,
            j.next_attempt_at,
            v.video_id,
            v.channel_id,
            v.title,
            v.upload_time,
            v.transcript_target_language
        FROM video_jobs j
        JOIN videos v ON v.video_id = j.video_id
        WHERE j.kind = 'transcript'
          AND j.status = 'queued'
          AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= datetime('now'))
        ORDER BY
            CASE j.origin WHEN 'manual' THEN 0 ELSE 1 END,
            CASE j.origin WHEN 'manual' THEN j.requested_at END ASC,
            v.upload_time DESC,
            v.created_at DESC,
            j.id ASC
        LIMIT ?
        """,
        (safe_lookahead,),
    )
    candidates = _rows_to_dicts(await cursor.fetchall())
    if not blocked_channel:
        return candidates[:safe_limit]
    preferred = [row for row in candidates if str(row.get("channel_id") or "") != blocked_channel]
    if len(preferred) >= safe_limit:
        return preferred[:safe_limit]
    return (preferred + [row for row in candidates if row not in preferred])[:safe_limit]


async def mark_transcript_job_running(db: aiosqlite.Connection, job_id: int) -> int:
    cursor = await db.execute(
        """
        UPDATE video_jobs
        SET status = 'running',
            started_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND kind = 'transcript'
          AND status = 'queued'
        """,
        (int(job_id),),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def recover_running_transcript_jobs(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE video_jobs
        SET status = 'queued',
            updated_at = datetime('now')
        WHERE kind = 'transcript'
          AND status = 'running'
        """
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def complete_transcript_job(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    video_id: str,
    raw_text: str,
    language: str | None,
    source_type: str,
    thumbnail_path: str | None = None,
    timed_text: str | None = None,
) -> None:
    """Store the transcript, close the job and queue the article when targeted."""
    await db.execute(
        f"""
        INSERT INTO transcripts(video_id, raw_text, timed_text, language, source_type)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            raw_text=excluded.raw_text,
            timed_text=excluded.timed_text,
            language=excluded.language,
            source_type=excluded.source_type,
            updated_at={UPDATED_AT_SQL}
        """,
        (video_id, raw_text, timed_text, language, source_type),
    )
    await db.execute(
        """
        UPDATE videos
        SET transcript_target_language = COALESCE(?, transcript_target_language),
            thumbnail_path = COALESCE(?, thumbnail_path)
        WHERE video_id = ?
        """,
        (language, thumbnail_path, video_id),
    )
    closed = await db.execute(
        """
        UPDATE video_jobs
        SET status = 'succeeded',
            next_attempt_at = NULL,
            error_code = NULL,
            error_message = NULL,
            error_at = NULL,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status IN ('queued', 'running')
        """,
        (int(job_id),),
    )
    if int(closed.rowcount or 0) > 0:
        await db.execute(
            """
            INSERT OR IGNORE INTO video_jobs(video_id, kind, origin, status)
            SELECT v.video_id, 'article', j.origin, 'queued'
            FROM videos v
            JOIN video_jobs j ON j.id = ?
            WHERE v.video_id = ?
              AND v.target_stage = 'article'
              AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.video_id = v.video_id)
            """,
            (int(job_id), video_id),
        )
    await db.commit()


async def schedule_transcript_job_retry(
    db: aiosqlite.Connection,
    job_id: int,
    *,
    delay_seconds: int,
    error_code: str,
    error_message: str | None = None,
) -> int:
    safe_delay = max(1, int(delay_seconds))
    cursor = await db.execute(
        """
        UPDATE video_jobs
        SET status = 'queued',
            attempt_count = attempt_count + 1,
            next_attempt_at = datetime('now', ?),
            error_code = ?,
            error_message = ?,
            error_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND kind = 'transcript'
          AND status IN ('queued', 'running')
        """,
        (
            f"+{safe_delay} seconds",
            _normalize_error_code(error_code),
            _normalize_error_message(error_message) or None,
            int(job_id),
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def fail_transcript_job(
    db: aiosqlite.Connection,
    job_id: int,
    *,
    attempt_count: int,
    error_code: str,
    error_message: str | None = None,
) -> int:
    cursor = await db.execute(
        """
        UPDATE video_jobs
        SET status = 'failed',
            attempt_count = ?,
            next_attempt_at = NULL,
            error_code = ?,
            error_message = ?,
            error_at = datetime('now'),
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND kind = 'transcript'
          AND status IN ('queued', 'running')
        """,
        (
            max(0, int(attempt_count)),
            _normalize_error_code(error_code),
            _normalize_error_message(error_message) or None,
            int(job_id),
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def defer_channel_transcript_jobs(
    db: aiosqlite.Connection,
    channel_id: str,
    delay_seconds: int,
    exclude_video_id: str | None = None,
) -> int:
    safe_channel = str(channel_id).strip()
    if not safe_channel:
        return 0
    delay = f"+{max(1, int(delay_seconds))} seconds"
    safe_excluded = str(exclude_video_id or "").strip()
    cursor = await db.execute(
        """
        UPDATE video_jobs
        SET next_attempt_at = CASE
                WHEN next_attempt_at IS NULL OR next_attempt_at < datetime('now', ?)
                THEN datetime('now', ?)
                ELSE next_attempt_at
            END,
            updated_at = datetime('now')
        WHERE kind = 'transcript'
          AND status IN ('queued', 'running')
          AND video_id IN (SELECT video_id FROM videos WHERE channel_id = ?)
          AND (? = '' OR video_id != ?)
        """,
        (delay, delay, safe_channel, safe_excluded, safe_excluded),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


# --- article jobs ----------------------------------------------------------------


async def claim_next_article_job(
    db: aiosqlite.Connection,
    max_attempts: int,
) -> dict[str, Any] | None:
    """Claim the next queued article job; settle jobs that can never run."""
    safe_max_attempts = max(1, int(max_attempts))
    while True:
        cursor = await db.execute(
            """
            SELECT
                j.id AS job_id,
                j.origin,
                j.attempt_count,
                v.video_id,
                v.title,
                t.video_id IS NOT NULL AS has_transcript,
                t.raw_text,
                t.timed_text
            FROM video_jobs j
            JOIN videos v ON v.video_id = j.video_id
            LEFT JOIN transcripts t ON t.video_id = j.video_id
            WHERE j.kind = 'article'
              AND j.status = 'queued'
              AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= datetime('now'))
            ORDER BY
                CASE j.origin WHEN 'manual' THEN 0 ELSE 1 END,
                v.created_at ASC,
                j.id ASC
            LIMIT 1
            """
        )
        candidate = _row_to_dict(await cursor.fetchone())
        if candidate is None:
            return None
        job_id = int(candidate["job_id"])
        if not candidate.get("has_transcript"):
            await _settle_queued_article_job(db, job_id, ERROR_CODE_TRANSCRIPT_MISSING)
            continue
        if int(candidate.get("attempt_count") or 0) >= safe_max_attempts:
            await _settle_queued_article_job(db, job_id, ERROR_CODE_RETRY_EXHAUSTED)
            continue
        claimed = await db.execute(
            """
            UPDATE video_jobs
            SET status = 'running',
                started_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
              AND status = 'queued'
            """,
            (job_id,),
        )
        await db.commit()
        if int(claimed.rowcount or 0) == 0:
            return None
        return candidate


async def _settle_queued_article_job(
    db: aiosqlite.Connection,
    job_id: int,
    error_code: str,
) -> None:
    await db.execute(
        """
        UPDATE video_jobs
        SET status = 'failed',
            error_code = ?,
            error_message = NULL,
            error_at = datetime('now'),
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'queued'
        """,
        (error_code, job_id),
    )
    await db.commit()
    logger.warning(
        "event=pipeline.article_job_settled job_id=%s code=%s",
        job_id,
        error_code,
        extra={"event": "pipeline.article_job_settled", "code": error_code},
    )


async def close_article_job_succeeded(db: aiosqlite.Connection, job_id: int) -> int:
    """Mark a running article job succeeded. Caller owns the transaction."""
    cursor = await db.execute(
        """
        UPDATE video_jobs
        SET status = 'succeeded',
            error_code = NULL,
            error_message = NULL,
            error_at = NULL,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND kind = 'article'
          AND status = 'running'
        """,
        (int(job_id),),
    )
    return int(cursor.rowcount or 0)


async def record_article_job_failure(
    db: aiosqlite.Connection,
    job_id: int,
    *,
    max_attempts: int,
    error_code: str,
    error_message: str | None = None,
    retryable: bool = True,
) -> tuple[str, int]:
    """Requeue a failed article attempt or fail the job once attempts run out.

    Returns the resulting job status (``queued`` or ``failed``) and affected rows.
    """
    safe_max_attempts = max(1, int(max_attempts))
    cursor = await db.execute(
        "SELECT attempt_count FROM video_jobs WHERE id = ? AND status = 'running'",
        (int(job_id),),
    )
    row = await cursor.fetchone()
    if row is None:
        return JOB_STATUS_FAILED, 0
    next_attempt = int(row["attempt_count"] or 0) + 1
    next_status = (
        JOB_STATUS_QUEUED if retryable and next_attempt < safe_max_attempts else JOB_STATUS_FAILED
    )
    updated = await db.execute(
        """
        UPDATE video_jobs
        SET status = ?,
            attempt_count = ?,
            error_code = ?,
            error_message = ?,
            error_at = datetime('now'),
            finished_at = CASE WHEN ? = 'failed' THEN datetime('now') ELSE NULL END,
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (
            next_status,
            next_attempt,
            _normalize_error_code(error_code),
            _normalize_error_message(error_message) or None,
            next_status,
            int(job_id),
        ),
    )
    await db.commit()
    return next_status, int(updated.rowcount or 0)


async def requeue_article_job(db: aiosqlite.Connection, job_id: int) -> int:
    """Return a running article job to the queue without spending an attempt."""
    cursor = await db.execute(
        """
        UPDATE video_jobs
        SET status = 'queued',
            started_at = NULL,
            error_code = NULL,
            error_message = NULL,
            error_at = NULL,
            updated_at = datetime('now')
        WHERE id = ?
          AND kind = 'article'
          AND status = 'running'
        """,
        (int(job_id),),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def count_queued_article_jobs(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM video_jobs j
        WHERE j.kind = 'article'
          AND j.status = 'queued'
          AND EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = j.video_id)
        """
    )
    row = await cursor.fetchone()
    return int((row["cnt"] if row is not None else 0) or 0)


# --- queue operations ------------------------------------------------------------

_ARTIFACT_TABLE_BY_KIND = {
    JOB_KIND_TRANSCRIPT: "transcripts",
    JOB_KIND_ARTICLE: "articles",
}


def _require_kind(kind: str) -> str:
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind: {kind}")
    return kind


async def cancel_queue(db: aiosqlite.Connection, kind: str) -> int:
    """Cancel queued jobs and still-current failures of ``kind``. Facts are untouched."""
    safe_kind = _require_kind(kind)
    artifact_table = _ARTIFACT_TABLE_BY_KIND[safe_kind]
    cursor = await db.execute(
        f"""
        UPDATE video_jobs
        SET status = 'cancelled',
            next_attempt_at = NULL,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE kind = ?
          AND (
              status = 'queued'
              OR (
                  status = 'failed'
                  AND {_LATEST_JOB_PREDICATE}
                  AND NOT EXISTS (
                      SELECT 1 FROM {artifact_table} x WHERE x.video_id = video_jobs.video_id
                  )
              )
          )
        """,
        (safe_kind,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def _requeue_failed_jobs(
    db: aiosqlite.Connection,
    kind: str,
    *,
    origin: str,
    video_id: str | None = None,
    extra_condition: str = "",
) -> int:
    """Open a fresh job for videos whose current ``kind`` job failed. Caller commits."""
    safe_kind = _require_kind(kind)
    artifact_table = _ARTIFACT_TABLE_BY_KIND[safe_kind]
    video_filter = "AND video_jobs.video_id = ?" if video_id is not None else ""
    params: tuple[str, ...] = (origin, safe_kind) + ((video_id,) if video_id is not None else ())
    cursor = await db.execute(
        f"""
        INSERT OR IGNORE INTO video_jobs(video_id, kind, origin, status)
        SELECT video_jobs.video_id, video_jobs.kind, ?, 'queued'
        FROM video_jobs
        WHERE video_jobs.kind = ?
          AND video_jobs.status = 'failed'
          AND {_LATEST_JOB_PREDICATE}
          AND NOT EXISTS (
              SELECT 1 FROM {artifact_table} x WHERE x.video_id = video_jobs.video_id
          )
          {video_filter}
          {extra_condition}
        """,
        params,
    )
    return int(cursor.rowcount or 0)


async def _reset_retrying_article_jobs(
    db: aiosqlite.Connection,
    *,
    video_id: str | None = None,
    origin: str | None = None,
) -> int:
    """Give queued article jobs waiting on a retry a full attempt budget. Caller commits.

    ``origin`` re-labels the job (a per-video retry is a manual request and is claimed first).
    """
    video_filter = "AND video_id = ?" if video_id is not None else ""
    params: list[str] = [] if origin is None else [origin]
    if video_id is not None:
        params.append(video_id)
    cursor = await db.execute(
        f"""
        UPDATE video_jobs
        SET attempt_count = 0,
            origin = {"?" if origin is not None else "origin"},
            next_attempt_at = NULL,
            error_code = NULL,
            error_message = NULL,
            error_at = NULL,
            updated_at = datetime('now')
        WHERE kind = 'article'
          AND status = 'queued'
          AND error_code IS NOT NULL
          {video_filter}
        """,
        params,
    )
    return int(cursor.rowcount or 0)


async def retry_failed_jobs(db: aiosqlite.Connection, kind: str) -> int:
    safe_kind = _require_kind(kind)
    retried = await _requeue_failed_jobs(db, safe_kind, origin=JOB_ORIGIN_AUTO)
    if safe_kind == JOB_KIND_ARTICLE:
        retried += await _reset_retrying_article_jobs(db)
    await db.commit()
    return retried


async def retry_article_for_video(db: aiosqlite.Connection, video_id: str) -> int:
    safe_video_id = str(video_id).strip()
    retried = await _requeue_failed_jobs(
        db, JOB_KIND_ARTICLE, origin=JOB_ORIGIN_MANUAL, video_id=safe_video_id
    )
    retried += await _reset_retrying_article_jobs(
        db, video_id=safe_video_id, origin=JOB_ORIGIN_MANUAL
    )
    await db.commit()
    return retried


async def retry_transcript_for_video(db: aiosqlite.Connection, video_id: str) -> int:
    retried = await _requeue_failed_jobs(
        db,
        JOB_KIND_TRANSCRIPT,
        origin=JOB_ORIGIN_MANUAL,
        video_id=str(video_id).strip(),
        extra_condition="""
          AND NOT EXISTS (
              SELECT 1 FROM video_jobs aj
              WHERE aj.video_id = video_jobs.video_id
                AND aj.kind = 'article'
                AND aj.status IN ('queued', 'running')
          )
        """,
    )
    await db.commit()
    return retried


async def _get_pipeline_row(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        f"SELECT * FROM {PIPELINE_VIEW_NAME} WHERE video_id = ?",
        (video_id,),
    )
    return _row_to_dict(await cursor.fetchone())


async def _insert_job(
    db: aiosqlite.Connection,
    video_id: str,
    kind: str,
    origin: str,
) -> int | None:
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO video_jobs(video_id, kind, origin, status)
        VALUES (?, ?, ?, 'queued')
        """,
        (video_id, _require_kind(kind), origin),
    )
    if int(cursor.rowcount or 0) == 0:
        return None
    return int(cursor.lastrowid or 0)


async def enqueue_manual_transcript_job(
    db: aiosqlite.Connection,
    video_id: str,
) -> dict[str, Any]:
    normalized_video_id = str(video_id or "").strip()
    if not normalized_video_id:
        return {"status": "failed", "reason": "invalid_video_id", "job_id": None}

    row = await _get_pipeline_row(db, normalized_video_id)
    if row is None:
        return {"status": "failed", "reason": "not_found", "job_id": None}
    if row.get("has_transcript"):
        return {"status": "skipped", "reason": "has_transcript", "job_id": None}
    if row.get("transcript_job_status") in {"queued", "running"} and (
        row.get("transcript_job_origin") == JOB_ORIGIN_MANUAL
    ):
        return {
            "status": "skipped",
            "reason": "active_job_exists",
            "job_id": int(row["transcript_job_id"]),
        }
    pipeline_status = str(row.get("pipeline_status") or "")
    if pipeline_status not in MANUAL_TRANSCRIPT_ALLOWED_PIPELINE_STATUSES:
        return {"status": "failed", "reason": f"pipeline_status:{pipeline_status}", "job_id": None}

    job_id = await _insert_job(db, normalized_video_id, JOB_KIND_TRANSCRIPT, JOB_ORIGIN_MANUAL)
    await db.commit()
    if job_id is None:
        return {"status": "skipped", "reason": "active_job_exists", "job_id": None}
    logger.info(
        "event=pipeline.manual_transcript_enqueue video_id=%s job_id=%s",
        normalized_video_id,
        job_id,
        extra={"event": "pipeline.manual_transcript_enqueue"},
    )
    return {"status": "queued", "reason": "", "job_id": job_id}


async def enqueue_manual_article_jobs(
    db: aiosqlite.Connection,
    video_ids: list[str],
) -> dict[str, Any]:
    """Raise videos to the article target and queue the next missing step."""
    summary: dict[str, list[str]] = {"new": [], "retry": [], "skip": [], "failed": []}
    requested_ids = list(video_ids or [])
    seen_ids: set[str] = set()
    for raw_video_id in requested_ids:
        video_id = str(raw_video_id or "").strip()
        if not video_id:
            summary["failed"].append("")
            continue
        if video_id in seen_ids:
            summary["skip"].append(video_id)
            continue
        seen_ids.add(video_id)

        row = await _get_pipeline_row(db, video_id)
        if row is None:
            summary["failed"].append(video_id)
            continue
        pipeline_status = str(row.get("pipeline_status") or "")
        if row.get("article_job_status") == "queued" and row.get("article_error_code"):
            # Waiting on an automatic retry: restart it now with a full attempt budget.
            await _reset_retrying_article_jobs(db, video_id=video_id)
            await db.execute(
                """
                UPDATE video_jobs SET origin = 'manual'
                WHERE video_id = ? AND kind = 'article' AND status = 'queued'
                """,
                (video_id,),
            )
            summary["retry"].append(video_id)
            continue
        if pipeline_status in MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES:
            summary["skip"].append(video_id)
            continue

        await db.execute(
            "UPDATE videos SET target_stage = 'article' WHERE video_id = ?",
            (video_id,),
        )
        kind = JOB_KIND_ARTICLE if row.get("has_transcript") else JOB_KIND_TRANSCRIPT
        job_id = await _insert_job(db, video_id, kind, JOB_ORIGIN_MANUAL)
        if job_id is None:
            summary["skip"].append(video_id)
            continue
        # "retry" when this step was attempted before for the video, "new" otherwise.
        prior_job_id = row.get(f"{kind}_job_id")
        summary["retry" if prior_job_id is not None else "new"].append(video_id)

    await db.commit()
    payload: dict[str, Any] = {
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
        "event=pipeline.manual_article_enqueue requested=%s new=%s retry=%s skip=%s failed=%s",
        len(requested_ids),
        payload["new_count"],
        payload["retry_count"],
        payload["skip_count"],
        payload["failed_count"],
        extra={"event": "pipeline.manual_article_enqueue"},
    )
    return payload


async def requeue_article_for_done_video(db: aiosqlite.Connection, video_id: str) -> int:
    """Regenerate the article of a finished video. The current article stays until replaced."""
    safe_video_id = str(video_id).strip()
    row = await _get_pipeline_row(db, safe_video_id)
    if (
        row is None
        or row.get("pipeline_status") != "article_ready"
        or not row.get("has_transcript")
    ):
        return 0
    job_id = await _insert_job(db, safe_video_id, JOB_KIND_ARTICLE, JOB_ORIGIN_MANUAL)
    await db.commit()
    return 0 if job_id is None else 1


# --- derived status reads --------------------------------------------------------


async def get_pipeline_state(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    return await _get_pipeline_row(db, str(video_id).strip())


async def queue_status(db: aiosqlite.Connection) -> dict[str, int]:
    """Video counts per status plus active job counts per kind and activity."""
    payload: dict[str, int] = {status: 0 for status in PIPELINE_STATUSES}
    for kind in JOB_KINDS:
        for activity in ("queued", "retry_wait", "running"):
            payload[f"{kind}_{activity}"] = 0
    cursor = await db.execute(
        f"""
        SELECT pipeline_status, pipeline_activity, activity_kind, COUNT(1) AS cnt
        FROM {PIPELINE_VIEW_NAME}
        GROUP BY pipeline_status, pipeline_activity, activity_kind
        """
    )
    for row in await cursor.fetchall():
        count = int(row["cnt"] or 0)
        status = str(row["pipeline_status"] or "")
        if status in payload:
            payload[status] += count
        activity_key = f"{row['activity_kind']}_{row['pipeline_activity']}"
        if row["activity_kind"] and activity_key in payload:
            payload[activity_key] += count
    # Transcript retry waits are still pending work (as before); article retry waits were
    # the separate llm_failed state and stay out of the badge.
    payload["badge_count"] = payload["transcript_retry_wait"] + sum(
        payload[f"{kind}_{activity}"] for kind in JOB_KINDS for activity in ("queued", "running")
    )
    return payload


async def list_queue_items(
    db: aiosqlite.Connection,
    kind: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Videos whose current ``kind`` job is active, or failed with the artifact missing."""
    safe_kind = _require_kind(kind)
    artifact_flag = "has_transcript" if safe_kind == JOB_KIND_TRANSCRIPT else "has_article"
    safe_limit = max(1, min(500, int(limit)))
    cursor = await db.execute(
        f"""
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            p.pipeline_status,
            p.pipeline_activity,
            p.{safe_kind}_job_status AS job_status,
            p.{safe_kind}_attempt_count AS attempt_count,
            p.{safe_kind}_error_code AS error_code,
            p.{safe_kind}_error_message AS error_message,
            v.created_at
        FROM {PIPELINE_VIEW_NAME} p
        JOIN videos v ON v.video_id = p.video_id
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE p.{safe_kind}_job_status IN ('queued', 'running')
           OR (p.{safe_kind}_job_status = 'failed' AND NOT p.{artifact_flag})
        ORDER BY
            CASE p.{safe_kind}_job_status
                WHEN 'running' THEN 0
                WHEN 'queued' THEN 1
                ELSE 2
            END,
            v.created_at ASC
        LIMIT ?
        """,
        (safe_limit,),
    )
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(await cursor.fetchall())]
