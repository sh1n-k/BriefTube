from __future__ import annotations

from typing import Any

import aiosqlite

from app.repositories._common import rows_to_dicts as _rows_to_dicts
from app.repositories._downloads import (
    DOWNLOAD_STATUS_FAILED,
    DOWNLOAD_STATUS_PENDING,
    DOWNLOAD_STATUS_RUNNING,
    DOWNLOAD_STATUS_SUCCEEDED,
    normalize_download_status_filter,
)


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
