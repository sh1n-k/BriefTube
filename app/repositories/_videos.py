from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import aiosqlite

from app.pipeline_status import VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES
from app.repositories._common import (
    row_to_dict as _row_to_dict,
)
from app.repositories._common import (
    rows_to_dicts as _rows_to_dicts,
)
from app.repositories._common import (
    with_thumbnail_url as _with_thumbnail_url,
)


def normalize_pipeline_status_filter(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES:
        return normalized
    return None


async def insert_video_if_absent(
    db: aiosqlite.Connection,
    video_id: str,
    channel_id: str,
    title: str,
    upload_time: str,
) -> bool:
    cursor = await db.execute(
        """
        INSERT INTO videos (
            video_id,
            channel_id,
            title,
            upload_time,
            pipeline_status,
            processing_stage_snapshot
        )
        SELECT
            ?,
            ?,
            ?,
            ?,
            CASE
                WHEN lower(trim(coalesce(cat.processing_stage, ''))) = 'off'
                THEN 'auto_paused'
                ELSE 'transcript_pending'
            END,
            CASE lower(trim(coalesce(cat.processing_stage, '')))
                WHEN 'off' THEN 'off'
                WHEN 'transcript_only' THEN 'transcript_only'
                WHEN 'full' THEN 'full'
                ELSE 'full'
            END
        FROM channels ch
        LEFT JOIN categories cat ON cat.id = ch.category_id
        WHERE ch.channel_id = ?
        ON CONFLICT(video_id) DO NOTHING
        """,
        (video_id, channel_id, title, upload_time, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0


_INSERT_VIDEO_BATCH_SQL = """
    INSERT INTO videos (
        video_id,
        channel_id,
        title,
        upload_time,
        pipeline_status,
        processing_stage_snapshot
    )
    SELECT
        ?,
        ?,
        ?,
        ?,
        CASE
            WHEN lower(trim(coalesce(cat.processing_stage, ''))) = 'off'
            THEN 'auto_paused'
            ELSE 'transcript_pending'
        END,
        CASE lower(trim(coalesce(cat.processing_stage, '')))
            WHEN 'off' THEN 'off'
            WHEN 'transcript_only' THEN 'transcript_only'
            WHEN 'full' THEN 'full'
            ELSE 'full'
        END
    FROM channels ch
    LEFT JOIN categories cat ON cat.id = ch.category_id
    WHERE ch.channel_id = ?
    ON CONFLICT(video_id) DO NOTHING
"""


async def insert_videos_if_absent_batch(
    db: aiosqlite.Connection,
    rows: Iterable[tuple[str, str, str, str]],
) -> int:
    """여러 RSS entry를 단일 ``executemany`` + 1 ``commit``으로 삽입한다.

    반환값은 실제로 INSERT된 행 수(``ON CONFLICT DO NOTHING``로 무시된 행은
    제외)다. ``rows``는 ``(video_id, channel_id, title, upload_time)`` 튜플
    이터러블이며 빈 이터러블은 no-op.
    """
    rows_list = [
        (str(video_id).strip(), str(channel_id).strip(), title, upload_time)
        for video_id, channel_id, title, upload_time in rows
        if str(video_id).strip() and str(channel_id).strip()
    ]
    if not rows_list:
        return 0
    params = [
        (video_id, channel_id, title, upload_time, channel_id)
        for video_id, channel_id, title, upload_time in rows_list
    ]
    cursor = await db.executemany(_INSERT_VIDEO_BATCH_SQL, params)
    await db.commit()
    return int(cursor.rowcount or 0)


async def list_videos(
    db: aiosqlite.Connection,
    channel_id: str | None,
    sort: str,
    order: str,
    page: int,
    limit: int,
    category_id: int | None = None,
    pipeline_status: str | None = None,
) -> list[dict[str, Any]]:
    sort_column = "upload_time" if sort not in {"upload_time", "created_at"} else sort
    order_sql = "ASC" if order.lower() == "asc" else "DESC"
    offset = (max(page, 1) - 1) * max(limit, 1)

    conditions: list[str] = []
    params: list[object] = []
    if channel_id:
        conditions.append("v.channel_id = ?")
        params.append(channel_id)
    if category_id is not None:
        conditions.append("c.category_id = ?")
        params.append(category_id)
    if pipeline_status:
        conditions.append("v.pipeline_status = ?")
        params.append(pipeline_status)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

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
            v.retry_count,
            v.created_at,
            v.viewed_at,
            EXISTS(
                SELECT 1
                FROM transcripts t
                WHERE t.video_id = v.video_id
            ) AS has_transcript,
            EXISTS(
                SELECT 1
                FROM articles a
                WHERE a.video_id = v.video_id
            ) AS has_article
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        {where_clause}
        ORDER BY v.{sort_column} {order_sql}
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )

    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def count_videos(
    db: aiosqlite.Connection,
    channel_id: str | None = None,
    category_id: int | None = None,
    pipeline_status: str | None = None,
) -> int:
    conditions: list[str] = []
    params: list[object] = []
    if channel_id:
        conditions.append("v.channel_id = ?")
        params.append(channel_id)
    if category_id is not None:
        conditions.append("c.category_id = ?")
        params.append(category_id)
    if pipeline_status:
        conditions.append("v.pipeline_status = ?")
        params.append(pipeline_status)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    cursor = await db.execute(
        f"""
        SELECT COUNT(1) AS cnt
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        {where_clause}
        """,
        tuple(params),
    )
    row = await cursor.fetchone()
    if row is None:
        return 0
    return int(row["cnt"] or 0)


async def get_video(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.retry_count,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE v.video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    raw = _row_to_dict(row)
    return _with_thumbnail_url(raw) if raw else None


async def list_videos_by_ids(
    db: aiosqlite.Connection,
    video_ids: list[str],
) -> list[dict[str, Any]]:
    normalized = [video_id for video_id in dict.fromkeys(video_ids) if str(video_id).strip()]
    if not normalized:
        return []

    placeholders = ",".join(["?"] * len(normalized))
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
            v.retry_count,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE v.video_id IN ({placeholders})
        """,
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def mark_video_viewed(db: aiosqlite.Connection, video_id: str) -> bool:
    cursor = await db.execute(
        "UPDATE videos SET viewed_at = datetime('now') WHERE video_id = ? AND viewed_at IS NULL",
        (video_id,),
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0


async def get_video_detail(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.retry_count,
            v.created_at,
            t.raw_text,
            t.language,
            t.source_type,
            a.title AS article_title,
            a.lead,
            a.body,
            a.fact_box,
            a.timestamps,
            a.llm_provider,
            a.llm_model,
            a.llm_reasoning_effort,
            a.llm_generated_at,
            mtj.id AS manual_transcript_job_id,
            mtj.status AS manual_transcript_status,
            mtj.error_message AS manual_transcript_error,
            mtj.retry_count AS manual_transcript_retry_count,
            mtj.requested_at AS manual_transcript_requested_at,
            mtj.started_at AS manual_transcript_started_at,
            mtj.finished_at AS manual_transcript_finished_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        LEFT JOIN transcripts t ON t.video_id = v.video_id
        LEFT JOIN articles a ON a.video_id = v.video_id
        LEFT JOIN manual_transcript_jobs mtj ON mtj.id = (
            SELECT id
            FROM manual_transcript_jobs
            WHERE video_id = v.video_id
              AND (
                  status IN ('pending', 'running')
                  OR status = 'failed'
              )
            ORDER BY
                CASE status
                    WHEN 'running' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'failed' THEN 2
                    ELSE 3
                END,
                updated_at DESC,
                id DESC
            LIMIT 1
        )
        WHERE v.video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    raw = _row_to_dict(row)
    return _with_thumbnail_url(raw) if raw else None


async def get_transcript(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT video_id, raw_text, language, source_type, created_at
        FROM transcripts
        WHERE video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_article(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            video_id,
            title,
            lead,
            body,
            fact_box,
            timestamps,
            llm_provider,
            llm_model,
            llm_reasoning_effort,
            llm_generated_at,
            created_at
        FROM articles
        WHERE video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def search_documents(
    db: aiosqlite.Connection, query: str, limit: int = 20
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT * FROM (
            SELECT
                'transcript' AS source,
                t.video_id AS video_id,
                v.title AS video_title,
                substr(t.raw_text, 1, 240) AS snippet,
                t.created_at AS created_at
            FROM transcripts_fts f
            JOIN transcripts t ON t.id = f.rowid
            JOIN videos v ON v.video_id = t.video_id
            WHERE transcripts_fts MATCH ?
            UNION ALL
            SELECT
                'article' AS source,
                a.video_id AS video_id,
                a.title AS video_title,
                substr(a.body, 1, 240) AS snippet,
                a.created_at AS created_at
            FROM articles_fts af
            JOIN articles a ON a.id = af.rowid
            WHERE articles_fts MATCH ?
        )
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (query, query, limit),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def mark_video_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending',
            retry_count = 0
        WHERE video_id = ? AND pipeline_status IN ('llm_failed', 'manual_review')
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def requeue_done_video_for_manual_article_retry(
    db: aiosqlite.Connection, video_id: str
) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending',
            retry_count = 0
        WHERE video_id = ?
          AND pipeline_status = 'done'
          AND EXISTS (
              SELECT 1
              FROM transcripts t
              WHERE t.video_id = videos.video_id
          )
        """,
        (video_id,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def delete_videos_by_ids(
    db: aiosqlite.Connection,
    video_ids: list[str],
) -> dict[str, Any]:
    normalized = [video_id for video_id in dict.fromkeys(video_ids) if video_id]
    if not normalized:
        return {"deleted": 0, "thumbnail_paths": []}

    placeholders = ",".join(["?"] * len(normalized))

    cursor = await db.execute(
        f"""
        SELECT thumbnail_path
        FROM videos
        WHERE video_id IN ({placeholders})
        """,
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    thumbnail_paths = [str(row["thumbnail_path"]) for row in rows if row["thumbnail_path"]]

    await db.execute(
        f"DELETE FROM articles WHERE video_id IN ({placeholders})",
        tuple(normalized),
    )
    await db.execute(
        f"DELETE FROM transcripts WHERE video_id IN ({placeholders})",
        tuple(normalized),
    )
    cursor = await db.execute(
        f"DELETE FROM videos WHERE video_id IN ({placeholders})",
        tuple(normalized),
    )
    await db.commit()
    return {"deleted": int(cursor.rowcount or 0), "thumbnail_paths": thumbnail_paths}
