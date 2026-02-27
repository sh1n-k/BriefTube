from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import aiosqlite

WORKER_SETTING_KEY_MAP: dict[str, str] = {
    "rss": "worker_rss_enabled",
    "transcript": "worker_transcript_enabled",
    "llm": "worker_llm_enabled",
    "notifier": "worker_notifier_enabled",
}

WORKER_SETTING_DEFAULTS: dict[str, bool] = {
    "rss": True,
    "transcript": True,
    "llm": True,
    "notifier": True,
}

ALERT_TYPE_RSS_CHANNEL_NOT_FOUND = "rss_channel_not_found"

RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY = "rss_bootstrap_lookback_days"
RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT = 60
RETENTION_DAYS_KEY = "retention_days"
RETENTION_DAYS_DEFAULT = 180
RSS_FEED_MODE_KEY = "rss_feed_mode"
RSS_FEED_MODE_DEFAULT = "long_form_only"
RSS_FEED_MODE_OPTIONS = {"all", "long_form_only"}
VIDEOS_PER_PAGE_KEY = "videos_per_page"
VIDEOS_PER_PAGE_DEFAULT = 8
TRANSCRIPT_GUARD_DEFAULTS: dict[str, str] = {
    "transcript_guard_adaptive_factor": "1.0",
    "transcript_guard_cooldown_until": "",
    "transcript_guard_consecutive_hard_errors": "0",
    "transcript_guard_consecutive_successes": "0",
    "transcript_guard_breaker_state": "closed",
    "transcript_guard_half_open_probe_remaining": "1",
    "transcript_guard_last_channel_id": "",
    "transcript_guard_last_channel_attempt_at": "",
}
TRANSCRIPT_ERROR_MESSAGE_MAX_LENGTH = 512
TRANSCRIPT_WORKER_LEASE_OWNER_KEY = "transcript_worker_lease_owner"
TRANSCRIPT_WORKER_LEASE_UNTIL_KEY = "transcript_worker_lease_until"
TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY = "transcript_request_headers_overrides_json"


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _rows_to_dicts(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{k: row[k] for k in row.keys()} for row in rows]


def _thumbnail_url(path: str | None) -> str | None:
    if not path:
        return None
    filename = Path(path).name
    if not filename:
        return None
    return f"/thumbnails/{filename}"


def _parse_bool_setting(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int_setting(value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _parse_float_setting(value: str | None, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _with_thumbnail_url(item: dict[str, Any]) -> dict[str, Any]:
    item["thumbnail_url"] = _thumbnail_url(item.get("thumbnail_path"))
    return item


def _parse_datetime_setting(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_error_message(value: str | None) -> str:
    if not value:
        return ""
    trimmed = str(value).strip()
    if len(trimmed) <= TRANSCRIPT_ERROR_MESSAGE_MAX_LENGTH:
        return trimmed
    return trimmed[:TRANSCRIPT_ERROR_MESSAGE_MAX_LENGTH]


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
            SELECT
                v.video_id,
                v.channel_id,
                c.channel_name AS channel_name,
                v.title,
                v.upload_time,
                v.thumbnail_path,
                v.transcript_status,
                v.restructure_status,
                v.retry_count,
                v.created_at
            FROM videos v
            LEFT JOIN channels c ON c.channel_id = v.channel_id
            WHERE v.channel_id = ?
            ORDER BY v.{sort_column} {order_sql}
            LIMIT ? OFFSET ?
            """,
            (channel_id, limit, offset),
        )
    else:
        cursor = await db.execute(
            f"""
            SELECT
                v.video_id,
                v.channel_id,
                c.channel_name AS channel_name,
                v.title,
                v.upload_time,
                v.thumbnail_path,
                v.transcript_status,
                v.restructure_status,
                v.retry_count,
                v.created_at
            FROM videos v
            LEFT JOIN channels c ON c.channel_id = v.channel_id
            ORDER BY v.{sort_column} {order_sql}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )

    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def count_videos(
    db: aiosqlite.Connection,
    channel_id: str | None = None,
) -> int:
    if channel_id:
        cursor = await db.execute(
            "SELECT COUNT(1) AS cnt FROM videos WHERE channel_id = ?",
            (channel_id,),
        )
    else:
        cursor = await db.execute("SELECT COUNT(1) AS cnt FROM videos")
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
            v.transcript_status,
            v.restructure_status,
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


async def pop_pending_transcript_videos(
    db: aiosqlite.Connection,
    limit: int = 3,
    *,
    lookahead: int | None = None,
    avoid_channel_id: str | None = None,
) -> list[dict[str, Any]]:
    safe_limit = max(1, int(limit))
    safe_lookahead = max(safe_limit, int(lookahead or safe_limit))
    blocked_channel = str(avoid_channel_id or "").strip()
    cursor = await db.execute(
        """
        SELECT
            video_id,
            channel_id,
            title,
            upload_time,
            transcript_retry_count,
            transcript_next_attempt_at,
            transcript_target_language
        FROM videos
        WHERE transcript_status = 'pending'
          AND (
              transcript_next_attempt_at IS NULL
              OR transcript_next_attempt_at <= datetime('now')
          )
        ORDER BY upload_time DESC, created_at DESC
        LIMIT ?
        """,
        (safe_lookahead,),
    )
    rows = await cursor.fetchall()
    candidates = _rows_to_dicts(rows)
    if not blocked_channel:
        return candidates[:safe_limit]

    preferred = [row for row in candidates if str(row.get("channel_id") or "") != blocked_channel]
    if len(preferred) >= safe_limit:
        return preferred[:safe_limit]

    return (preferred + candidates)[:safe_limit]


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
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_target_language = COALESCE(?, transcript_target_language),
            transcript_last_error = NULL,
            transcript_last_error_at = NULL,
            thumbnail_path = COALESCE(?, thumbnail_path)
        WHERE video_id = ?
        """,
        (language, thumbnail_path, video_id),
    )
    await db.commit()


async def update_video_thumbnail(
    db: aiosqlite.Connection,
    video_id: str,
    thumbnail_path: str | None,
) -> None:
    if not thumbnail_path:
        return
    await db.execute(
        """
        UPDATE videos
        SET thumbnail_path = ?
        WHERE video_id = ?
        """,
        (thumbnail_path, video_id),
    )
    await db.commit()


async def mark_no_subtitle(db: aiosqlite.Connection, video_id: str) -> None:
    await db.execute(
        """
        UPDATE videos
        SET transcript_status = 'no_subtitle',
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_last_error = NULL,
            transcript_last_error_at = NULL
        WHERE video_id = ?
        """,
        (video_id,),
    )
    await db.commit()


async def schedule_transcript_retry(
    db: aiosqlite.Connection,
    video_id: str,
    delay_seconds: int,
    error_message: str | None = None,
) -> int:
    safe_delay = max(1, int(delay_seconds))
    safe_error = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE videos
        SET transcript_retry_count = transcript_retry_count + 1,
            transcript_next_attempt_at = datetime('now', ?),
            transcript_last_error = ?,
            transcript_last_error_at = datetime('now')
        WHERE video_id = ?
          AND transcript_status = 'pending'
        """,
        (f"+{safe_delay} seconds", safe_error, video_id),
    )
    await db.commit()
    return cursor.rowcount


async def defer_channel_transcript_retries(
    db: aiosqlite.Connection,
    channel_id: str,
    delay_seconds: int,
    exclude_video_id: str | None = None,
) -> int:
    safe_channel = str(channel_id).strip()
    if not safe_channel:
        return 0
    safe_delay = max(1, int(delay_seconds))
    safe_excluded = str(exclude_video_id or "").strip()
    cursor = await db.execute(
        """
        UPDATE videos
        SET transcript_next_attempt_at = CASE
            WHEN transcript_next_attempt_at IS NULL
                 OR transcript_next_attempt_at < datetime('now', ?)
            THEN datetime('now', ?)
            ELSE transcript_next_attempt_at
        END
        WHERE transcript_status = 'pending'
          AND channel_id = ?
          AND (? = '' OR video_id != ?)
        """,
        (
            f"+{safe_delay} seconds",
            f"+{safe_delay} seconds",
            safe_channel,
            safe_excluded,
            safe_excluded,
        ),
    )
    await db.commit()
    return cursor.rowcount


async def mark_transcript_failed(
    db: aiosqlite.Connection,
    video_id: str,
    retry_count: int,
    error_message: str | None = None,
) -> int:
    safe_retry = max(0, int(retry_count))
    safe_error = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE videos
        SET transcript_status = 'failed',
            transcript_retry_count = ?,
            transcript_next_attempt_at = NULL,
            transcript_last_error = ?,
            transcript_last_error_at = datetime('now')
        WHERE video_id = ?
          AND transcript_status IN ('pending', 'failed', 'no_subtitle')
        """,
        (safe_retry, safe_error, video_id),
    )
    await db.commit()
    return cursor.rowcount


async def reset_transcript_for_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET transcript_status = 'pending',
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_last_error = NULL,
            transcript_last_error_at = NULL
        WHERE video_id = ?
          AND transcript_status IN ('failed', 'no_subtitle')
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


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


async def get_setting(db: aiosqlite.Connection, key: str, default: str | None = None) -> str | None:
    cursor = await db.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (key,),
    )
    row = await cursor.fetchone()
    if row is None:
        return default
    return str(row["value"])


async def set_setting(db: aiosqlite.Connection, key: str, value: str) -> None:
    await db.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        (key, value),
    )
    await db.commit()


async def get_transcript_request_header_overrides(db: aiosqlite.Connection) -> dict[str, str]:
    raw = await get_setting(
        db,
        key=TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY,
        default="{}",
    )
    text = str(raw or "").strip() or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    overrides: dict[str, str] = {}
    for key, value in parsed.items():
        overrides[str(key).strip()] = str(value).strip()
    return overrides


async def save_transcript_request_header_overrides(
    db: aiosqlite.Connection,
    overrides: dict[str, str],
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in overrides.items():
        normalized[str(key).strip()] = str(value).strip()
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    await set_setting(
        db,
        key=TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY,
        value=payload,
    )
    return await get_transcript_request_header_overrides(db)


async def acquire_transcript_worker_lease(
    db: aiosqlite.Connection,
    owner_id: str,
    ttl_seconds: int,
) -> bool:
    safe_owner = str(owner_id).strip()
    if not safe_owner:
        return False
    ttl = max(5, int(ttl_seconds))
    now = datetime.now(timezone.utc)
    await db.execute("BEGIN IMMEDIATE")
    try:
        owner_cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY,),
        )
        owner_row = await owner_cursor.fetchone()
        until_cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (TRANSCRIPT_WORKER_LEASE_UNTIL_KEY,),
        )
        until_row = await until_cursor.fetchone()
        current_owner = str(owner_row["value"] if owner_row is not None else "").strip()
        current_until = _parse_datetime_setting(str(until_row["value"] if until_row is not None else ""))

        if current_owner and current_owner != safe_owner and current_until and current_until > now:
            await db.rollback()
            return False

        lease_until = now + timedelta(seconds=ttl)
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY, safe_owner),
        )
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_UNTIL_KEY, lease_until.isoformat()),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def renew_transcript_worker_lease(
    db: aiosqlite.Connection,
    owner_id: str,
    ttl_seconds: int,
) -> bool:
    safe_owner = str(owner_id).strip()
    if not safe_owner:
        return False
    await db.execute("BEGIN IMMEDIATE")
    try:
        cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY,),
        )
        row = await cursor.fetchone()
        current_owner = str(row["value"] if row is not None else "").strip()
        if current_owner != safe_owner:
            await db.rollback()
            return False
        ttl = max(5, int(ttl_seconds))
        lease_until = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_UNTIL_KEY, lease_until.isoformat()),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def release_transcript_worker_lease(db: aiosqlite.Connection, owner_id: str) -> bool:
    safe_owner = str(owner_id).strip()
    if not safe_owner:
        return False
    await db.execute("BEGIN IMMEDIATE")
    try:
        cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY,),
        )
        row = await cursor.fetchone()
        current_owner = str(row["value"] if row is not None else "").strip()
        if current_owner != safe_owner:
            await db.rollback()
            return False
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, '')
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY,),
        )
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, '')
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_UNTIL_KEY,),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def get_transcript_guard_state(db: aiosqlite.Connection) -> dict[str, Any]:
    adaptive_raw = await get_setting(
        db,
        "transcript_guard_adaptive_factor",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_adaptive_factor"],
    )
    cooldown_raw = await get_setting(
        db,
        "transcript_guard_cooldown_until",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_cooldown_until"],
    )
    hard_raw = await get_setting(
        db,
        "transcript_guard_consecutive_hard_errors",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_consecutive_hard_errors"],
    )
    success_raw = await get_setting(
        db,
        "transcript_guard_consecutive_successes",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_consecutive_successes"],
    )
    breaker_state_raw = await get_setting(
        db,
        "transcript_guard_breaker_state",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_breaker_state"],
    )
    probe_raw = await get_setting(
        db,
        "transcript_guard_half_open_probe_remaining",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_half_open_probe_remaining"],
    )
    last_channel_raw = await get_setting(
        db,
        "transcript_guard_last_channel_id",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_last_channel_id"],
    )
    last_channel_attempt_raw = await get_setting(
        db,
        "transcript_guard_last_channel_attempt_at",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_last_channel_attempt_at"],
    )

    cooldown_until = str(cooldown_raw or "").strip() or None
    breaker_state = str(breaker_state_raw or "closed").strip().lower() or "closed"
    if breaker_state not in {"closed", "open", "half_open"}:
        breaker_state = "closed"
    last_channel_attempt = _parse_datetime_setting(str(last_channel_attempt_raw or ""))
    return {
        "adaptive_factor": _parse_float_setting(adaptive_raw, default=1.0, min_value=1.0, max_value=64.0),
        "cooldown_until": cooldown_until,
        "consecutive_hard_errors": _parse_int_setting(hard_raw, default=0, min_value=0, max_value=100000),
        "consecutive_successes": _parse_int_setting(success_raw, default=0, min_value=0, max_value=100000),
        "breaker_state": breaker_state,
        "half_open_probe_remaining": _parse_int_setting(probe_raw, default=1, min_value=1, max_value=1000),
        "last_channel_id": str(last_channel_raw or "").strip() or None,
        "last_channel_attempt_at": last_channel_attempt.isoformat() if last_channel_attempt else None,
    }


async def save_transcript_guard_state(
    db: aiosqlite.Connection,
    adaptive_factor: float,
    cooldown_until: str | None,
    consecutive_hard_errors: int,
    consecutive_successes: int,
    breaker_state: str = "closed",
    half_open_probe_remaining: int = 1,
    last_channel_id: str | None = None,
    last_channel_attempt_at: str | None = None,
) -> dict[str, Any]:
    safe_breaker_state = str(breaker_state).strip().lower()
    if safe_breaker_state not in {"closed", "open", "half_open"}:
        safe_breaker_state = "closed"
    entries = {
        "transcript_guard_adaptive_factor": str(max(1.0, float(adaptive_factor))),
        "transcript_guard_cooldown_until": str(cooldown_until or ""),
        "transcript_guard_consecutive_hard_errors": str(max(0, int(consecutive_hard_errors))),
        "transcript_guard_consecutive_successes": str(max(0, int(consecutive_successes))),
        "transcript_guard_breaker_state": safe_breaker_state,
        "transcript_guard_half_open_probe_remaining": str(max(1, int(half_open_probe_remaining))),
        "transcript_guard_last_channel_id": str(last_channel_id or "").strip(),
        "transcript_guard_last_channel_attempt_at": str(last_channel_attempt_at or "").strip(),
    }
    for key, value in entries.items():
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (key, value),
        )
    await db.commit()
    return await get_transcript_guard_state(db)


async def reset_transcript_guard_state(db: aiosqlite.Connection) -> dict[str, Any]:
    return await save_transcript_guard_state(
        db,
        adaptive_factor=1.0,
        cooldown_until=None,
        consecutive_hard_errors=0,
        consecutive_successes=0,
        breaker_state="closed",
        half_open_probe_remaining=1,
        last_channel_id=None,
        last_channel_attempt_at=None,
    )


async def get_worker_settings(db: aiosqlite.Connection) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for worker, key in WORKER_SETTING_KEY_MAP.items():
        default = WORKER_SETTING_DEFAULTS.get(worker, True)
        raw = await get_setting(db, key=key, default="true" if default else "false")
        result[worker] = _parse_bool_setting(raw, default=default)
    return result


async def set_worker_settings(db: aiosqlite.Connection, values: dict[str, bool]) -> dict[str, bool]:
    for worker, enabled in values.items():
        key = WORKER_SETTING_KEY_MAP.get(worker)
        if not key:
            continue
        await set_setting(db, key=key, value="true" if bool(enabled) else "false")
    return await get_worker_settings(db)


async def is_worker_enabled(db: aiosqlite.Connection, worker: str) -> bool:
    key = WORKER_SETTING_KEY_MAP.get(worker)
    if not key:
        return True
    default = WORKER_SETTING_DEFAULTS.get(worker, True)
    raw = await get_setting(db, key=key, default="true" if default else "false")
    return _parse_bool_setting(raw, default=default)


async def get_policy_settings(db: aiosqlite.Connection) -> dict[str, int | str]:
    lookback_raw = await get_setting(
        db,
        key=RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY,
        default=str(RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT),
    )
    retention_raw = await get_setting(
        db,
        key=RETENTION_DAYS_KEY,
        default=str(RETENTION_DAYS_DEFAULT),
    )
    feed_mode_raw = await get_setting(db, key=RSS_FEED_MODE_KEY, default=RSS_FEED_MODE_DEFAULT)
    feed_mode = str(feed_mode_raw).strip().lower() if feed_mode_raw else RSS_FEED_MODE_DEFAULT
    if feed_mode not in RSS_FEED_MODE_OPTIONS:
        feed_mode = RSS_FEED_MODE_DEFAULT
    return {
        "rss_bootstrap_lookback_days": _parse_int_setting(
            lookback_raw,
            default=RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT,
            min_value=1,
            max_value=3650,
        ),
        "retention_days": _parse_int_setting(
            retention_raw,
            default=RETENTION_DAYS_DEFAULT,
            min_value=1,
            max_value=3650,
        ),
        "rss_feed_mode": feed_mode,
    }


async def set_policy_settings(
    db: aiosqlite.Connection,
    rss_bootstrap_lookback_days: int | None = None,
    retention_days: int | None = None,
    rss_feed_mode: str | None = None,
) -> dict[str, int | str]:
    current = await get_policy_settings(db)
    lookback_value = current["rss_bootstrap_lookback_days"]
    retention_value = current["retention_days"]

    if rss_bootstrap_lookback_days is not None:
        lookback_value = _parse_int_setting(
            str(rss_bootstrap_lookback_days),
            default=lookback_value,
            min_value=1,
            max_value=3650,
        )
        await set_setting(db, key=RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY, value=str(lookback_value))

    if retention_days is not None:
        retention_value = _parse_int_setting(
            str(retention_days),
            default=retention_value,
            min_value=1,
            max_value=3650,
        )
        await set_setting(db, key=RETENTION_DAYS_KEY, value=str(retention_value))

    if rss_feed_mode is not None:
        normalized = str(rss_feed_mode).strip().lower()
        if normalized not in RSS_FEED_MODE_OPTIONS:
            normalized = RSS_FEED_MODE_DEFAULT
        await set_setting(db, key=RSS_FEED_MODE_KEY, value=normalized)

    return await get_policy_settings(db)


async def get_videos_per_page_setting(db: aiosqlite.Connection) -> int:
    raw = await get_setting(
        db,
        key=VIDEOS_PER_PAGE_KEY,
        default=str(VIDEOS_PER_PAGE_DEFAULT),
    )
    return _parse_int_setting(
        raw,
        default=VIDEOS_PER_PAGE_DEFAULT,
        min_value=1,
        max_value=100,
    )


async def set_videos_per_page_setting(db: aiosqlite.Connection, value: int) -> int:
    normalized = _parse_int_setting(
        str(value),
        default=VIDEOS_PER_PAGE_DEFAULT,
        min_value=1,
        max_value=100,
    )
    await set_setting(db, key=VIDEOS_PER_PAGE_KEY, value=str(normalized))
    return normalized


async def create_system_alert(
    db: aiosqlite.Connection,
    alert_type: str,
    message: str,
    channel_id: str | None = None,
    channel_name: str | None = None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO system_alerts(alert_type, channel_id, channel_name, message)
        VALUES (?, ?, ?, ?)
        """,
        (alert_type, channel_id, channel_name, message),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def list_unacknowledged_alerts(
    db: aiosqlite.Connection,
    limit: int = 5,
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT id, alert_type, channel_id, channel_name, message, created_at
        FROM system_alerts
        WHERE acknowledged_at IS NULL
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, limit),),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def acknowledge_alert(db: aiosqlite.Connection, alert_id: int) -> int:
    cursor = await db.execute(
        """
        UPDATE system_alerts
        SET acknowledged_at = datetime('now')
        WHERE id = ?
          AND acknowledged_at IS NULL
        """,
        (alert_id,),
    )
    await db.commit()
    return cursor.rowcount


async def count_retention_expired_videos(db: aiosqlite.Connection, retention_days: int) -> int:
    modifier = f"-{max(1, int(retention_days))} days"
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos
        WHERE datetime(upload_time) <= datetime('now', ?)
        """,
        (modifier,),
    )
    row = await cursor.fetchone()
    return int((row["cnt"] if row else 0) or 0)


async def list_retention_expired_video_ids(db: aiosqlite.Connection, retention_days: int) -> list[str]:
    modifier = f"-{max(1, int(retention_days))} days"
    cursor = await db.execute(
        """
        SELECT video_id
        FROM videos
        WHERE datetime(upload_time) <= datetime('now', ?)
        ORDER BY datetime(upload_time) ASC, created_at ASC
        """,
        (modifier,),
    )
    rows = await cursor.fetchall()
    return [str(row["video_id"]) for row in rows]


async def list_retention_expired_videos(db: aiosqlite.Connection, retention_days: int) -> list[dict[str, Any]]:
    modifier = f"-{max(1, int(retention_days))} days"
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.transcript_status,
            v.restructure_status,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE datetime(v.upload_time) <= datetime('now', ?)
        ORDER BY datetime(v.upload_time) ASC, v.created_at ASC
        """,
        (modifier,),
    )
    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def delete_videos_by_ids(
    db: aiosqlite.Connection,
    video_ids: list[str],
) -> dict[str, Any]:
    normalized = [video_id for video_id in dict.fromkeys(video_ids) if video_id]
    if not normalized:
        return {"deleted": 0, "thumbnail_paths": []}

    placeholders = ",".join(["?"] * len(normalized))

    cursor = await db.execute(
        f"SELECT thumbnail_path FROM videos WHERE video_id IN ({placeholders})",
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


async def delete_channels_with_related_data(
    db: aiosqlite.Connection,
    channel_ids: list[str],
) -> dict[str, Any]:
    normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not normalized:
        return {"deleted_channels": 0, "deleted_videos": 0, "thumbnail_paths": []}

    placeholders = ",".join(["?"] * len(normalized))

    cursor = await db.execute(
        f"SELECT thumbnail_path FROM videos WHERE channel_id IN ({placeholders})",
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    thumbnail_paths = [str(row["thumbnail_path"]) for row in rows if row["thumbnail_path"]]

    await db.execute(
        f"""
        DELETE FROM articles
        WHERE video_id IN (
            SELECT video_id FROM videos WHERE channel_id IN ({placeholders})
        )
        """,
        tuple(normalized),
    )
    await db.execute(
        f"""
        DELETE FROM transcripts
        WHERE video_id IN (
            SELECT video_id FROM videos WHERE channel_id IN ({placeholders})
        )
        """,
        tuple(normalized),
    )
    videos_cursor = await db.execute(
        f"DELETE FROM videos WHERE channel_id IN ({placeholders})",
        tuple(normalized),
    )
    channels_cursor = await db.execute(
        f"DELETE FROM channels WHERE channel_id IN ({placeholders})",
        tuple(normalized),
    )
    await db.commit()

    return {
        "deleted_channels": int(channels_cursor.rowcount or 0),
        "deleted_videos": int(videos_cursor.rowcount or 0),
        "thumbnail_paths": thumbnail_paths,
    }


def is_newer_published(candidate: str, watermark: str | None) -> bool:
    if watermark is None:
        return True

    try:
        candidate_dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        watermark_dt = datetime.fromisoformat(watermark.replace("Z", "+00:00"))
    except ValueError:
        return candidate > watermark
    return candidate_dt > watermark_dt
