from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

from app.download_policy import validate_download_output_dir
from app.repositories import _settings as settings_repository

get_settings_map = settings_repository.get_settings_map
set_setting = settings_repository.set_setting

DOWNLOAD_ERROR_MESSAGE_MAX_LENGTH = 512


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def _parse_bool_setting(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_error_message(value: str | None) -> str:
    if not value:
        return ""
    trimmed = str(value).strip()
    if len(trimmed) <= DOWNLOAD_ERROR_MESSAGE_MAX_LENGTH:
        return trimmed
    return trimmed[:DOWNLOAD_ERROR_MESSAGE_MAX_LENGTH]


DOWNLOAD_DEFAULT_QUALITY_KEY = "download_default_quality"
DOWNLOAD_DEFAULT_OVERWRITE_KEY = "download_default_overwrite"
DOWNLOAD_DEFAULT_OUTPUT_DIR_KEY = "download_output_dir"
DOWNLOAD_QUALITY_DEFAULT = "1080"
DOWNLOAD_OUTPUT_DIR_DEFAULT = "./downloads"
DOWNLOAD_QUALITY_OPTIONS = {"2160", "1440", "1080", "720", "480"}
DOWNLOAD_STATUS_PENDING = "pending"
DOWNLOAD_STATUS_RUNNING = "running"
DOWNLOAD_STATUS_SUCCEEDED = "succeeded"
DOWNLOAD_STATUS_FAILED = "failed"
DOWNLOAD_STATUS_OPTIONS = {
    DOWNLOAD_STATUS_PENDING,
    DOWNLOAD_STATUS_RUNNING,
    DOWNLOAD_STATUS_SUCCEEDED,
    DOWNLOAD_STATUS_FAILED,
}


def _normalize_download_quality(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in DOWNLOAD_QUALITY_OPTIONS:
        return normalized
    return DOWNLOAD_QUALITY_DEFAULT


def normalize_download_quality(value: str | None) -> str:
    return _normalize_download_quality(value)


def normalize_download_status_filter(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in DOWNLOAD_STATUS_OPTIONS:
        return normalized
    return "all"


async def get_download_default_settings(
    db: aiosqlite.Connection,
    *,
    default_output_dir: str | None = None,
) -> dict[str, Any]:
    default_dir_raw = (
        str(default_output_dir or DOWNLOAD_OUTPUT_DIR_DEFAULT).strip()
        or DOWNLOAD_OUTPUT_DIR_DEFAULT
    )
    settings = await get_settings_map(
        db,
        {
            DOWNLOAD_DEFAULT_QUALITY_KEY: DOWNLOAD_QUALITY_DEFAULT,
            DOWNLOAD_DEFAULT_OVERWRITE_KEY: "false",
            DOWNLOAD_DEFAULT_OUTPUT_DIR_KEY: default_dir_raw,
        },
    )
    quality_raw = settings[DOWNLOAD_DEFAULT_QUALITY_KEY]
    overwrite_raw = settings[DOWNLOAD_DEFAULT_OVERWRITE_KEY]
    output_dir_raw = settings[DOWNLOAD_DEFAULT_OUTPUT_DIR_KEY]
    output_dir_candidate = str(output_dir_raw or "").strip() or default_dir_raw
    output_dir_path = Path(output_dir_candidate).expanduser()
    try:
        output_dir_resolved = output_dir_path.resolve(strict=False)
    except OSError:
        output_dir_resolved = Path(default_dir_raw).expanduser().resolve(strict=False)
    return {
        "quality": _normalize_download_quality(quality_raw),
        "overwrite": _parse_bool_setting(overwrite_raw, default=False),
        "output_dir": str(output_dir_resolved),
    }


async def set_download_default_settings(
    db: aiosqlite.Connection,
    *,
    quality: str | None = None,
    overwrite: bool | None = None,
    output_dir: str | None = None,
    default_output_dir: str | None = None,
) -> dict[str, Any]:
    current = await get_download_default_settings(
        db,
        default_output_dir=default_output_dir,
    )
    next_quality = (
        _normalize_download_quality(quality) if quality is not None else str(current["quality"])
    )
    next_overwrite = bool(overwrite) if overwrite is not None else bool(current["overwrite"])
    next_output_dir = (
        str(output_dir or "").strip() if output_dir is not None else str(current["output_dir"])
    )
    validation = validate_download_output_dir(
        next_output_dir,
        require_absolute=True,
        require_existing=True,
    )
    if not validation.ok:
        raise ValueError(validation.error_code or "download_path_invalid")
    await set_setting(db, key=DOWNLOAD_DEFAULT_QUALITY_KEY, value=next_quality)
    await set_setting(
        db, key=DOWNLOAD_DEFAULT_OVERWRITE_KEY, value="true" if next_overwrite else "false"
    )
    await set_setting(db, key=DOWNLOAD_DEFAULT_OUTPUT_DIR_KEY, value=validation.normalized_path)
    return await get_download_default_settings(
        db,
        default_output_dir=default_output_dir,
    )


async def _insert_download_event(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    event_type: str,
    error_code: str | None = None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO download_events(job_id, event_type, error_code)
        VALUES (?, ?, ?)
        """,
        (job_id, event_type, str(error_code or "").strip() or None),
    )
    return int(cursor.lastrowid or 0)


async def get_download_job(
    db: aiosqlite.Connection,
    job_id: int,
) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            id,
            video_id,
            video_title,
            status,
            quality,
            overwrite,
            target_dir,
            attempt_count,
            output_path,
            file_size_bytes,
            error_code,
            error_message,
            requested_at,
            started_at,
            finished_at,
            updated_at
        FROM download_jobs
        WHERE id = ?
        """,
        (int(job_id),),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_active_download_job_for_video(
    db: aiosqlite.Connection,
    video_id: str,
) -> dict[str, Any] | None:
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return None
    cursor = await db.execute(
        """
        SELECT
            id,
            video_id,
            video_title,
            status,
            quality,
            overwrite,
            target_dir,
            attempt_count,
            output_path,
            file_size_bytes,
            error_code,
            error_message,
            requested_at,
            started_at,
            finished_at,
            updated_at
        FROM download_jobs
        WHERE video_id = ?
          AND status IN ('pending', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        (normalized_video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def create_download_job(
    db: aiosqlite.Connection,
    *,
    video_id: str,
    video_title: str,
    quality: str,
    overwrite: bool,
    target_dir: str,
) -> dict[str, Any]:
    normalized_video_id = str(video_id).strip()
    normalized_title = str(video_title).strip() or normalized_video_id
    normalized_quality = _normalize_download_quality(quality)
    normalized_overwrite = 1 if bool(overwrite) else 0
    normalized_target_dir = str(target_dir).strip()

    existing = await get_active_download_job_for_video(db, normalized_video_id)
    if existing is not None:
        return {"created": False, "duplicate": True, "job": existing}

    try:
        cursor = await db.execute(
            """
            INSERT INTO download_jobs(
                video_id,
                video_title,
                status,
                quality,
                overwrite,
                target_dir,
                attempt_count,
                requested_at,
                updated_at
            )
            VALUES (?, ?, 'pending', ?, ?, ?, 1, datetime('now'), datetime('now'))
            """,
            (
                normalized_video_id,
                normalized_title,
                normalized_quality,
                normalized_overwrite,
                normalized_target_dir,
            ),
        )
        job_id = int(cursor.lastrowid or 0)
        await _insert_download_event(
            db,
            job_id=job_id,
            event_type="enqueued",
        )
        await db.commit()
        created = await get_download_job(db, job_id)
        return {"created": True, "duplicate": False, "job": created}
    except sqlite3.IntegrityError:
        existing = await get_active_download_job_for_video(db, normalized_video_id)
        if existing is not None:
            return {"created": False, "duplicate": True, "job": existing}
        raise


async def claim_next_download_job(db: aiosqlite.Connection) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT id
        FROM download_jobs
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
        UPDATE download_jobs
        SET
            status = 'running',
            started_at = COALESCE(started_at, datetime('now')),
            updated_at = datetime('now'),
            error_code = NULL,
            error_message = NULL
        WHERE id = ?
          AND status = 'pending'
        """,
        (job_id,),
    )
    if int(updated.rowcount or 0) == 0:
        return None

    await _insert_download_event(
        db,
        job_id=job_id,
        event_type="started",
    )
    await db.commit()
    return await get_download_job(db, job_id)


async def mark_download_job_succeeded(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    output_path: str | None,
    file_size_bytes: int | None,
) -> int:
    cursor = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'succeeded',
            output_path = ?,
            file_size_bytes = ?,
            error_code = NULL,
            error_message = NULL,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (
            str(output_path or "").strip() or None,
            int(file_size_bytes) if file_size_bytes is not None else None,
            int(job_id),
        ),
    )
    rowcount = int(cursor.rowcount or 0)
    if rowcount > 0:
        await _insert_download_event(
            db,
            job_id=int(job_id),
            event_type="succeeded",
        )
    await db.commit()
    return rowcount


async def mark_download_job_failed(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    error_code: str,
    error_message: str,
) -> int:
    normalized_error_code = str(error_code or "").strip().lower() or "unknown"
    normalized_error_message = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'failed',
            error_code = ?,
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (
            normalized_error_code,
            normalized_error_message,
            int(job_id),
        ),
    )
    rowcount = int(cursor.rowcount or 0)
    if rowcount > 0:
        await _insert_download_event(
            db,
            job_id=int(job_id),
            event_type="failed",
            error_code=normalized_error_code,
        )
    await db.commit()
    return rowcount


async def retry_download_job(db: aiosqlite.Connection, job_id: int) -> dict[str, Any]:
    safe_job_id = int(job_id)
    cursor = await db.execute(
        """
        SELECT id, status
        FROM download_jobs
        WHERE id = ?
        """,
        (safe_job_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {"updated": 0, "reason": "not_found"}

    status = str(row["status"] or "").strip().lower()
    if status != DOWNLOAD_STATUS_FAILED:
        return {"updated": 0, "reason": "invalid_status"}

    updated = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'pending',
            attempt_count = attempt_count + 1,
            error_code = NULL,
            error_message = NULL,
            requested_at = datetime('now'),
            started_at = NULL,
            finished_at = NULL,
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'failed'
        """,
        (safe_job_id,),
    )
    rowcount = int(updated.rowcount or 0)
    if rowcount <= 0:
        return {"updated": 0, "reason": "already_changed"}

    await _insert_download_event(
        db,
        job_id=safe_job_id,
        event_type="retried",
    )
    await db.commit()
    return {"updated": rowcount, "reason": "ok"}


async def recover_stuck_download_jobs(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        SELECT id
        FROM download_jobs
        WHERE status = 'running'
        ORDER BY id ASC
        """
    )
    rows = await cursor.fetchall()
    job_ids = [int(row["id"]) for row in rows]
    if not job_ids:
        return 0

    updated = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'failed',
            error_code = 'worker_interrupted',
            error_message = 'download worker interrupted (app restart/shutdown)',
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE status = 'running'
        """
    )
    for job_id in job_ids:
        await _insert_download_event(
            db,
            job_id=job_id,
            event_type="failed",
            error_code="worker_interrupted",
        )
    await db.commit()
    return int(updated.rowcount or 0)


async def count_download_jobs_by_status(db: aiosqlite.Connection) -> dict[str, int]:
    cursor = await db.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
        FROM download_jobs
        """
    )
    row = await cursor.fetchone()
    return {
        DOWNLOAD_STATUS_PENDING: int((row["pending_count"] if row else 0) or 0),
        DOWNLOAD_STATUS_RUNNING: int((row["running_count"] if row else 0) or 0),
        DOWNLOAD_STATUS_SUCCEEDED: int((row["succeeded_count"] if row else 0) or 0),
        DOWNLOAD_STATUS_FAILED: int((row["failed_count"] if row else 0) or 0),
    }


async def list_download_jobs(
    db: aiosqlite.Connection,
    *,
    status: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> list[dict[str, Any]]:
    normalized_status = normalize_download_status_filter(status)
    safe_limit = max(1, min(200, int(limit)))
    safe_page = max(1, int(page))
    offset = (safe_page - 1) * safe_limit
    if normalized_status == "all":
        cursor = await db.execute(
            """
            SELECT
                id,
                video_id,
                video_title,
                status,
                quality,
                overwrite,
                target_dir,
                attempt_count,
                output_path,
                file_size_bytes,
                error_code,
                error_message,
                requested_at,
                started_at,
                finished_at,
                updated_at
            FROM download_jobs
            ORDER BY requested_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (safe_limit, offset),
        )
    else:
        cursor = await db.execute(
            """
            SELECT
                id,
                video_id,
                video_title,
                status,
                quality,
                overwrite,
                target_dir,
                attempt_count,
                output_path,
                file_size_bytes,
                error_code,
                error_message,
                requested_at,
                started_at,
                finished_at,
                updated_at
            FROM download_jobs
            WHERE status = ?
            ORDER BY requested_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (
                normalized_status,
                safe_limit,
                offset,
            ),
        )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def count_download_jobs(
    db: aiosqlite.Connection,
    *,
    status: str | None = None,
) -> int:
    normalized_status = normalize_download_status_filter(status)
    if normalized_status == "all":
        cursor = await db.execute("SELECT COUNT(1) AS cnt FROM download_jobs")
    else:
        cursor = await db.execute(
            "SELECT COUNT(1) AS cnt FROM download_jobs WHERE status = ?",
            (normalized_status,),
        )
    row = await cursor.fetchone()
    return int((row["cnt"] if row else 0) or 0)


async def clear_download_jobs(
    db: aiosqlite.Connection,
    *,
    status: str | None = None,
) -> int:
    normalized_status = normalize_download_status_filter(status)
    if normalized_status in {DOWNLOAD_STATUS_PENDING, DOWNLOAD_STATUS_RUNNING}:
        return 0
    if normalized_status == "all":
        cursor = await db.execute(
            """
            DELETE FROM download_jobs
            WHERE status IN ('succeeded', 'failed')
            """
        )
    else:
        cursor = await db.execute(
            """
            DELETE FROM download_jobs
            WHERE status = ?
            """,
            (normalized_status,),
        )
    await db.commit()
    return int(cursor.rowcount or 0)


async def latest_download_event_id(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT MAX(id) AS max_id FROM download_events")
    row = await cursor.fetchone()
    return int((row["max_id"] if row else 0) or 0)


async def list_download_events_after(
    db: aiosqlite.Connection,
    *,
    after_event_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    safe_after = max(0, int(after_event_id))
    safe_limit = max(1, min(200, int(limit)))
    cursor = await db.execute(
        """
        SELECT
            e.id,
            e.job_id,
            e.event_type,
            e.error_code,
            e.created_at,
            j.video_id,
            j.video_title,
            j.status
        FROM download_events e
        JOIN download_jobs j ON j.id = e.job_id
        WHERE e.id > ?
        ORDER BY e.id ASC
        LIMIT ?
        """,
        (safe_after, safe_limit),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def get_download_progress(
    db: aiosqlite.Connection,
    *,
    after_event_id: int,
    event_limit: int = 100,
) -> dict[str, Any]:
    counts = await count_download_jobs_by_status(db)
    events = await list_download_events_after(
        db,
        after_event_id=after_event_id,
        limit=event_limit,
    )
    latest_event = await latest_download_event_id(db)
    return {
        "counts": counts,
        "events": events,
        "latest_event_id": latest_event,
    }
