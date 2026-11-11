from __future__ import annotations

import sqlite3
from typing import Any

_INSERT_JOB_SQL = """
    INSERT INTO video_jobs(
        video_id, kind, origin, status, attempt_count, next_attempt_at,
        error_code, error_message, error_at, started_at, finished_at
    )
    VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?,
        CASE WHEN ? IS NULL THEN NULL ELSE datetime('now') END,
        CASE WHEN ? = 'running' THEN datetime('now') END,
        CASE WHEN ? IN ('succeeded', 'failed', 'cancelled') THEN datetime('now') END
    )
"""


def _job_params(
    video_id: str,
    kind: str,
    *,
    status: str,
    origin: str,
    attempt_count: int,
    next_attempt_at: str | None,
    error_code: str | None,
    error_message: str | None,
) -> tuple[Any, ...]:
    return (
        video_id,
        kind,
        origin,
        status,
        attempt_count,
        next_attempt_at,
        error_code,
        error_message,
        error_code,
        status,
        status,
    )


async def seed_video_job(
    db: Any,
    video_id: str,
    kind: str,
    *,
    status: str = "queued",
    origin: str = "auto",
    attempt_count: int = 0,
    next_attempt_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    commit: bool = True,
) -> int:
    """Insert a video_jobs row (aiosqlite). Returns the job id."""
    cursor = await db.execute(
        _INSERT_JOB_SQL,
        _job_params(
            video_id,
            kind,
            status=status,
            origin=origin,
            attempt_count=attempt_count,
            next_attempt_at=next_attempt_at,
            error_code=error_code,
            error_message=error_message,
        ),
    )
    if commit:
        await db.commit()
    return int(cursor.lastrowid or 0)


def seed_video_job_sync(
    conn: sqlite3.Connection,
    video_id: str,
    kind: str,
    *,
    status: str = "queued",
    origin: str = "auto",
    attempt_count: int = 0,
    next_attempt_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> int:
    """Insert a video_jobs row (sqlite3). The caller commits."""
    cursor = conn.execute(
        _INSERT_JOB_SQL,
        _job_params(
            video_id,
            kind,
            status=status,
            origin=origin,
            attempt_count=attempt_count,
            next_attempt_at=next_attempt_at,
            error_code=error_code,
            error_message=error_message,
        ),
    )
    return int(cursor.lastrowid or 0)


async def seed_channel(
    db: Any,
    *,
    channel_id: str,
    channel_name: str = "Test Channel",
    rss_url: str | None = None,
    is_active: int = 1,
) -> None:
    await db.execute(
        """
        INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, ?)
        """,
        (
            channel_id,
            channel_name,
            rss_url or f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            is_active,
        ),
    )
    await db.commit()


async def seed_video(
    db: Any,
    *,
    video_id: str,
    channel_id: str,
    title: str | None = None,
    upload_time: str = "2026-02-26T00:00:00+00:00",
    target_stage: str = "article",
) -> None:
    await db.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, target_stage)
        VALUES (?, ?, ?, ?, ?)
        """,
        (video_id, channel_id, title or video_id, upload_time, target_stage),
    )
    await db.commit()


async def fetch_pipeline_status(db: Any, video_id: str) -> str | None:
    cursor = await db.execute(
        "SELECT pipeline_status FROM video_pipeline_v WHERE video_id = ?",
        (video_id,),
    )
    row = await cursor.fetchone()
    return None if row is None else str(row[0])
