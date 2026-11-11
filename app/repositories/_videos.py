from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable
from typing import Any

import aiosqlite

import app.repositories._pipeline as pipeline_repository
from app.database_migrations import PIPELINE_VIEW_NAME
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


def normalize_viewed_filter(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"unread", "read"}:
        return normalized
    return None


def _append_viewed_filter(
    conditions: list[str],
    viewed: str | None,
) -> None:
    if viewed == "unread":
        conditions.append("v.viewed_at IS NULL")
    elif viewed == "read":
        conditions.append("v.viewed_at IS NOT NULL")


logger = logging.getLogger("app.repositories.videos")

_SNIPPET_MARK_START = "\ue000"
_SNIPPET_MARK_END = "\ue001"


def _strip_markdown_for_snippet(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"[*_~]{1,3}", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _plain_search_snippet(raw: str | None) -> str:
    text = str(raw or "")
    if not text:
        return ""
    plain = text.replace(_SNIPPET_MARK_START, "").replace(_SNIPPET_MARK_END, "")
    return _strip_markdown_for_snippet(plain)


def _format_search_snippet(raw: str | None) -> str:
    text = str(raw or "")
    if not text:
        return ""
    placeholders: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\ue010{len(placeholders) - 1}\ue011"

    held = re.sub(
        re.escape(_SNIPPET_MARK_START) + r".*?" + re.escape(_SNIPPET_MARK_END),
        _hold,
        text,
        flags=re.DOTALL,
    )
    cleaned = _strip_markdown_for_snippet(held)
    escaped = html.escape(cleaned, quote=False)

    def _restore(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        original = placeholders[idx] if 0 <= idx < len(placeholders) else ""
        inner = original[len(_SNIPPET_MARK_START) : -len(_SNIPPET_MARK_END)]
        return f"<mark>{html.escape(inner, quote=False)}</mark>"

    return re.sub(r"\ue010(\d+)\ue011", _restore, escaped)


async def insert_video_if_absent(
    db: aiosqlite.Connection,
    video_id: str,
    channel_id: str,
    title: str,
    upload_time: str,
) -> bool:
    inserted = await _insert_video_with_initial_job(db, (video_id, channel_id, title, upload_time))
    await db.commit()
    return inserted


_INSERT_VIDEO_SQL = f"""
    INSERT INTO videos (
        video_id,
        channel_id,
        title,
        upload_time,
        target_stage
    )
    SELECT
        ?,
        ?,
        ?,
        ?,
        {pipeline_repository.TARGET_STAGE_FROM_CATEGORY_SQL}
    FROM channels ch
    LEFT JOIN categories cat ON cat.id = ch.category_id
    WHERE ch.channel_id = ?
    ON CONFLICT(video_id) DO NOTHING
"""


async def _insert_video_with_initial_job(
    db: aiosqlite.Connection,
    row: tuple[str, str, str, str],
) -> bool:
    video_id, channel_id, title, upload_time = row
    cursor = await db.execute(
        _INSERT_VIDEO_SQL,
        (video_id, channel_id, title, upload_time, channel_id),
    )
    if int(cursor.rowcount or 0) == 0:
        return False
    await pipeline_repository.enqueue_initial_transcript_job(db, video_id)
    return True


async def insert_videos_if_absent_batch(
    db: aiosqlite.Connection,
    rows: Iterable[tuple[str, str, str, str]],
) -> int:
    """여러 RSS entry를 1 ``commit``으로 삽입하고 새 영상에 첫 자막 작업을 연다.

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
    inserted = 0
    for row in rows_list:
        if await _insert_video_with_initial_job(db, row):
            inserted += 1
    await db.commit()
    return inserted


async def list_videos(
    db: aiosqlite.Connection,
    channel_id: str | None,
    sort: str,
    order: str,
    page: int,
    limit: int,
    category_id: int | None = None,
    pipeline_status: str | None = None,
    viewed: str | None = None,
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
        conditions.append("p.pipeline_status = ?")
        params.append(pipeline_status)
    _append_viewed_filter(conditions, viewed)
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
            p.pipeline_status,
            p.article_attempt_count AS retry_count,
            v.created_at,
            v.viewed_at,
            p.has_transcript,
            p.has_article,
            a.title AS article_title,
            substr(a.lead, 1, 400) AS article_lead
        FROM videos v
        JOIN {PIPELINE_VIEW_NAME} p ON p.video_id = v.video_id
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        LEFT JOIN articles a ON a.video_id = v.video_id
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
    viewed: str | None = None,
) -> int:
    conditions: list[str] = []
    params: list[object] = []
    if channel_id:
        conditions.append("v.channel_id = ?")
        params.append(channel_id)
    if category_id is not None:
        conditions.append("c.category_id = ?")
        params.append(category_id)
    status_join = ""
    if pipeline_status:
        conditions.append("p.pipeline_status = ?")
        params.append(pipeline_status)
        status_join = f"JOIN {PIPELINE_VIEW_NAME} p ON p.video_id = v.video_id"
    _append_viewed_filter(conditions, viewed)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    cursor = await db.execute(
        f"""
        SELECT COUNT(1) AS cnt
        FROM videos v
        {status_join}
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
        f"""
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            p.pipeline_status,
            p.article_attempt_count AS retry_count,
            v.created_at
        FROM videos v
        JOIN {PIPELINE_VIEW_NAME} p ON p.video_id = v.video_id
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
            p.pipeline_status,
            p.article_attempt_count AS retry_count,
            v.created_at
        FROM videos v
        JOIN {PIPELINE_VIEW_NAME} p ON p.video_id = v.video_id
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
        f"""
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            p.pipeline_status,
            p.article_attempt_count AS retry_count,
            p.transcript_attempt_count AS transcript_retry_count,
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
            CASE WHEN p.transcript_job_origin = 'manual' THEN p.transcript_job_id END
                AS manual_transcript_job_id,
            CASE
                WHEN p.transcript_job_origin != 'manual' THEN NULL
                WHEN p.transcript_job_status = 'queued' THEN 'pending'
                WHEN p.transcript_job_status IN ('running', 'failed') THEN p.transcript_job_status
            END AS manual_transcript_status,
            CASE WHEN p.transcript_job_origin = 'manual' AND p.transcript_job_status = 'failed'
                THEN coalesce(p.transcript_error_message, p.transcript_error_code)
            END AS manual_transcript_error,
            CASE WHEN p.transcript_job_origin = 'manual' THEN p.transcript_attempt_count END
                AS manual_transcript_retry_count,
            CASE WHEN p.transcript_job_origin = 'manual' THEN p.transcript_requested_at END
                AS manual_transcript_requested_at,
            CASE WHEN p.transcript_job_origin = 'manual' THEN p.transcript_started_at END
                AS manual_transcript_started_at,
            CASE WHEN p.transcript_job_origin = 'manual' THEN p.transcript_finished_at END
                AS manual_transcript_finished_at
        FROM videos v
        JOIN {PIPELINE_VIEW_NAME} p ON p.video_id = v.video_id
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        LEFT JOIN transcripts t ON t.video_id = v.video_id
        LEFT JOIN articles a ON a.video_id = v.video_id
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


def _is_malformed_fts_query_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "syntax error" in message
        or ("fts5" in message and "parse" in message)
        or "unterminated" in message
        or ("malformed" in message and "match" in message)
    )


async def search_documents(
    db: aiosqlite.Connection,
    query: str,
    limit: int = 20,
    *,
    highlight: bool = False,
) -> list[dict[str, Any]]:
    try:
        return await _search_documents(db, query=query, limit=limit, highlight=highlight)
    except aiosqlite.OperationalError as exc:
        if not _is_malformed_fts_query_error(exc):
            raise
        logger.warning(
            "event=videos.search_query_rejected error_type=%s",
            exc.__class__.__name__,
            extra={"event": "videos.search_query_rejected"},
        )
        return []


async def _search_documents(
    db: aiosqlite.Connection,
    query: str,
    limit: int,
    *,
    highlight: bool,
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT * FROM (
            SELECT
                'transcript' AS source,
                t.video_id AS video_id,
                v.title AS video_title,
                snippet(
                    transcripts_fts,
                    0,
                    ?,
                    ?,
                    '…',
                    32
                ) AS snippet,
                t.created_at AS created_at
            FROM transcripts_fts
            JOIN transcripts t ON t.id = transcripts_fts.rowid
            JOIN videos v ON v.video_id = t.video_id
            WHERE transcripts_fts MATCH ?
            UNION ALL
            SELECT
                'article' AS source,
                a.video_id AS video_id,
                a.title AS video_title,
                snippet(
                    articles_fts,
                    -1,
                    ?,
                    ?,
                    '…',
                    32
                ) AS snippet,
                a.created_at AS created_at
            FROM articles_fts
            JOIN articles a ON a.id = articles_fts.rowid
            WHERE articles_fts MATCH ?
        )
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            _SNIPPET_MARK_START,
            _SNIPPET_MARK_END,
            query,
            _SNIPPET_MARK_START,
            _SNIPPET_MARK_END,
            query,
            limit,
        ),
    )
    rows = await cursor.fetchall()
    items = _rows_to_dicts(rows)
    formatter = _format_search_snippet if highlight else _plain_search_snippet
    for item in items:
        item["snippet"] = formatter(item.get("snippet"))
    return items


async def delete_videos_by_ids(
    db: aiosqlite.Connection,
    video_ids: list[str],
    *,
    commit: bool = True,
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
    if commit:
        await db.commit()
    return {"deleted": int(cursor.rowcount or 0), "thumbnail_paths": thumbnail_paths}
