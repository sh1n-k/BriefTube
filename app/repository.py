from __future__ import annotations

from datetime import datetime
from typing import Any

import aiosqlite


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _rows_to_dicts(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{k: row[k] for k in row.keys()} for row in rows]


async def list_channels(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT channel_id, channel_name, rss_url, is_active, last_seen_published_at, created_at
        FROM channels
        ORDER BY created_at DESC
        """
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def add_channel(db: aiosqlite.Connection, channel_id: str, channel_name: str) -> dict[str, Any]:
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    await db.execute(
        """
        INSERT INTO channels (channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(channel_id) DO UPDATE SET
            channel_name=excluded.channel_name,
            rss_url=excluded.rss_url,
            is_active=1
        """,
        (channel_id, channel_name, rss_url),
    )
    await db.commit()

    cursor = await db.execute(
        """
        SELECT channel_id, channel_name, rss_url, is_active, last_seen_published_at, created_at
        FROM channels
        WHERE channel_id = ?
        """,
        (channel_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row) or {}


async def deactivate_channel(db: aiosqlite.Connection, channel_id: str) -> int:
    cursor = await db.execute(
        "UPDATE channels SET is_active = 0 WHERE channel_id = ?",
        (channel_id,),
    )
    await db.commit()
    return cursor.rowcount


async def list_active_channels(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT channel_id, channel_name, rss_url, is_active, last_seen_published_at, created_at
        FROM channels
        WHERE is_active = 1
        ORDER BY created_at ASC
        """
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def update_channel_watermark(db: aiosqlite.Connection, channel_id: str, published_at: str) -> None:
    await db.execute(
        "UPDATE channels SET last_seen_published_at = ? WHERE channel_id = ?",
        (published_at, channel_id),
    )
    await db.commit()


async def insert_video_if_absent(
    db: aiosqlite.Connection,
    video_id: str,
    channel_id: str,
    title: str,
    upload_time: str,
) -> bool:
    cursor = await db.execute(
        """
        INSERT INTO videos (video_id, channel_id, title, upload_time)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(video_id) DO NOTHING
        """,
        (video_id, channel_id, title, upload_time),
    )
    await db.commit()
    return cursor.rowcount > 0


async def list_videos(
    db: aiosqlite.Connection,
    channel_id: str | None,
    sort: str,
    order: str,
    page: int,
    limit: int,
) -> list[dict[str, Any]]:
    sort_column = "upload_time" if sort not in {"upload_time", "created_at"} else sort
    order_sql = "ASC" if order.lower() == "asc" else "DESC"
    offset = (max(page, 1) - 1) * max(limit, 1)

    if channel_id:
        cursor = await db.execute(
            f"""
            SELECT video_id, channel_id, title, upload_time, thumbnail_path,
                   transcript_status, restructure_status, retry_count, created_at
            FROM videos
            WHERE channel_id = ?
            ORDER BY {sort_column} {order_sql}
            LIMIT ? OFFSET ?
            """,
            (channel_id, limit, offset),
        )
    else:
        cursor = await db.execute(
            f"""
            SELECT video_id, channel_id, title, upload_time, thumbnail_path,
                   transcript_status, restructure_status, retry_count, created_at
            FROM videos
            ORDER BY {sort_column} {order_sql}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )

    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def get_video(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT video_id, channel_id, title, upload_time, thumbnail_path,
               transcript_status, restructure_status, retry_count, created_at
        FROM videos
        WHERE video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_video_detail(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.channel_id,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.transcript_status,
            v.restructure_status,
            v.retry_count,
            v.created_at,
            t.raw_text,
            t.language,
            t.source_type,
            a.title AS article_title,
            a.lead,
            a.body,
            a.fact_box,
            a.timestamps
        FROM videos v
        LEFT JOIN transcripts t ON t.video_id = v.video_id
        LEFT JOIN articles a ON a.video_id = v.video_id
        WHERE v.video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


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
        SELECT video_id, title, lead, body, fact_box, timestamps, created_at
        FROM articles
        WHERE video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def search_documents(db: aiosqlite.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
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


async def queue_status(db: aiosqlite.Connection) -> dict[str, int]:
    cursor = await db.execute(
        """
        SELECT
            SUM(CASE WHEN restructure_status='pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN restructure_status='processing' THEN 1 ELSE 0 END) AS processing,
            SUM(CASE WHEN restructure_status='failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN restructure_status='manual_review' THEN 1 ELSE 0 END) AS manual_review
        FROM videos
        """
    )
    row = await cursor.fetchone()
    if row is None:
        return {
            "pending": 0,
            "processing": 0,
            "failed": 0,
            "manual_review": 0,
        }

    return {
        "pending": int(row["pending"] or 0),
        "processing": int(row["processing"] or 0),
        "failed": int(row["failed"] or 0),
        "manual_review": int(row["manual_review"] or 0),
    }


async def mark_video_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET restructure_status = 'pending'
        WHERE video_id = ? AND restructure_status IN ('failed', 'manual_review')
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def pop_pending_transcript_videos(db: aiosqlite.Connection, limit: int = 3) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT video_id, channel_id, title, upload_time
        FROM videos
        WHERE transcript_status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def save_transcript(
    db: aiosqlite.Connection,
    video_id: str,
    raw_text: str,
    language: str | None,
    source_type: str,
    thumbnail_path: str | None,
) -> None:
    await db.execute(
        """
        INSERT INTO transcripts(video_id, raw_text, language, source_type)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            raw_text=excluded.raw_text,
            language=excluded.language,
            source_type=excluded.source_type
        """,
        (video_id, raw_text, language, source_type),
    )
    await db.execute(
        """
        UPDATE videos
        SET transcript_status = 'done',
            thumbnail_path = COALESCE(?, thumbnail_path)
        WHERE video_id = ?
        """,
        (thumbnail_path, video_id),
    )
    await db.commit()


async def mark_no_subtitle(db: aiosqlite.Connection, video_id: str) -> None:
    await db.execute(
        "UPDATE videos SET transcript_status = 'no_subtitle' WHERE video_id = ?",
        (video_id,),
    )
    await db.commit()


async def pop_llm_candidate(db: aiosqlite.Connection, max_retry_count: int) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.title,
            v.retry_count,
            t.raw_text
        FROM videos v
        JOIN transcripts t ON t.video_id = v.video_id
        WHERE v.transcript_status = 'done'
          AND v.restructure_status IN ('pending', 'failed')
          AND v.retry_count < ?
        ORDER BY v.created_at ASC
        LIMIT 1
        """,
        (max_retry_count,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def mark_restructure_processing(db: aiosqlite.Connection, video_id: str) -> None:
    await db.execute(
        "UPDATE videos SET restructure_status = 'processing' WHERE video_id = ?",
        (video_id,),
    )
    await db.commit()


async def save_article(
    db: aiosqlite.Connection,
    video_id: str,
    title: str,
    lead: str,
    body: str,
    fact_box: str | None,
    timestamps: str | None,
) -> None:
    await db.execute(
        """
        INSERT INTO articles(video_id, title, lead, body, fact_box, timestamps)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            title=excluded.title,
            lead=excluded.lead,
            body=excluded.body,
            fact_box=excluded.fact_box,
            timestamps=excluded.timestamps
        """,
        (video_id, title, lead, body, fact_box, timestamps),
    )
    await db.execute(
        "UPDATE videos SET restructure_status = 'done' WHERE video_id = ?",
        (video_id,),
    )
    await db.commit()


async def mark_restructure_failed(
    db: aiosqlite.Connection,
    video_id: str,
    retry_count: int,
    max_retry_count: int,
) -> str:
    next_retry = retry_count + 1
    next_status = "failed" if next_retry < max_retry_count else "manual_review"
    await db.execute(
        """
        UPDATE videos
        SET retry_count = ?, restructure_status = ?
        WHERE video_id = ?
        """,
        (next_retry, next_status, video_id),
    )
    await db.commit()
    return next_status


def is_newer_published(candidate: str, watermark: str | None) -> bool:
    if watermark is None:
        return True

    try:
        candidate_dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        watermark_dt = datetime.fromisoformat(watermark.replace("Z", "+00:00"))
    except ValueError:
        return candidate > watermark
    return candidate_dt > watermark_dt
