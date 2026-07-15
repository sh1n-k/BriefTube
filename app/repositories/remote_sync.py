from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import aiosqlite

from app.remote_sync_metadata import (
    REMOTE_SYNC_KEY_COLUMNS,
    REMOTE_SYNC_PRUNE_ORDER,
    REMOTE_SYNC_RUNTIME_ENABLED_KEY,
    REMOTE_SYNC_STATUS_LAST_FAILURE_CODE_KEY,
    REMOTE_SYNC_STATUS_LAST_SUCCESS_AT_KEY,
    REMOTE_SYNC_STATUS_SCHEMA_VERSION_KEY,
    REMOTE_SYNC_TABLES,
    SYNC_NOW_SQL,
    sync_dirty_set_clause,
)


def _rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def _is_remote_newer(remote: Mapping[str, Any], local: Mapping[str, Any] | None) -> bool:
    if local is None:
        return True
    remote_updated = str(remote.get("updated_at") or "")
    local_updated = str(local.get("updated_at") or "")
    if remote_updated != local_updated:
        return remote_updated > local_updated
    remote_deleted = remote.get("deleted_at") is not None and str(remote.get("deleted_at")) != ""
    local_deleted = local.get("deleted_at") is not None and str(local.get("deleted_at")) != ""
    if remote_deleted != local_deleted:
        return remote_deleted
    return str(remote.get("origin_device_id") or "") > str(local.get("origin_device_id") or "")


async def set_runtime_enabled(db: aiosqlite.Connection, enabled: bool) -> None:
    await db.execute(
        """
        INSERT INTO app_settings(key, value, updated_at)
        VALUES ('remote_sync_runtime_enabled', ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        ("1" if enabled else "0",),
    )
    await db.commit()


async def record_status(
    db: aiosqlite.Connection,
    *,
    success: bool,
    failure_code: str = "",
    schema_version_status: str = "ok",
) -> None:
    if success:
        await db.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (REMOTE_SYNC_STATUS_LAST_SUCCESS_AT_KEY,),
        )
        failure_code = ""
    await db.execute(
        """
        INSERT INTO app_settings(key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        (REMOTE_SYNC_STATUS_LAST_FAILURE_CODE_KEY, failure_code),
    )
    await db.execute(
        """
        INSERT INTO app_settings(key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        (REMOTE_SYNC_STATUS_SCHEMA_VERSION_KEY, schema_version_status),
    )
    await db.commit()


async def get_status(
    db: aiosqlite.Connection, *, configured: bool, requested: bool
) -> dict[str, Any]:
    cursor = await db.execute(
        """
        SELECT key, value
        FROM app_settings
        WHERE key IN (?, ?, ?, ?)
        """,
        (
            REMOTE_SYNC_RUNTIME_ENABLED_KEY,
            REMOTE_SYNC_STATUS_LAST_SUCCESS_AT_KEY,
            REMOTE_SYNC_STATUS_LAST_FAILURE_CODE_KEY,
            REMOTE_SYNC_STATUS_SCHEMA_VERSION_KEY,
        ),
    )
    rows = await cursor.fetchall()
    values = {str(row["key"]): str(row["value"] or "") for row in rows}
    return {
        "configured": configured,
        "requested": requested,
        "active": requested and values.get(REMOTE_SYNC_RUNTIME_ENABLED_KEY) == "1",
        "last_success_at": values.get(REMOTE_SYNC_STATUS_LAST_SUCCESS_AT_KEY, ""),
        "last_failure_code": values.get(REMOTE_SYNC_STATUS_LAST_FAILURE_CODE_KEY, ""),
        "schema_version_status": values.get(REMOTE_SYNC_STATUS_SCHEMA_VERSION_KEY, "unknown"),
    }


async def list_dirty_rows(
    db: aiosqlite.Connection, *, batch_size: int
) -> dict[str, list[dict[str, Any]]]:
    safe_limit = max(1, int(batch_size))
    remaining = safe_limit
    categories = await _fetch_all(
        db,
        """
        SELECT category_uid, name, sort_order, processing_stage, is_default, created_at,
               updated_at, deleted_at, sync_dirty, sync_last_pushed_at, origin_device_id
        FROM categories
        WHERE sync_dirty = 1
          AND category_uid IS NOT NULL
          AND trim(category_uid) != ''
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (remaining,),
    )
    remaining -= len(categories)
    channels = await _fetch_all(
        db,
        """
        SELECT c.channel_id, c.channel_name, c.rss_url, c.is_active, cat.category_uid,
               c.last_seen_published_at, c.channel_handle, c.channel_url_canonical,
               c.channel_thumbnail_url, c.channel_description, c.channel_language_hint,
               c.metadata_fetched_at, c.metadata_fetch_status, c.rss_priority,
               c.created_at, c.updated_at, c.deleted_at, c.sync_dirty,
               c.sync_last_pushed_at, c.origin_device_id
        FROM channels c
        LEFT JOIN categories cat ON cat.id = c.category_id
        WHERE c.sync_dirty = 1
        ORDER BY c.updated_at ASC
        LIMIT ?
        """,
        (remaining,),
    )
    remaining -= len(channels)
    videos = await _fetch_all(
        db,
        """
        SELECT video_id, channel_id, title, upload_time, created_at, updated_at, deleted_at,
               sync_dirty, sync_last_pushed_at, origin_device_id
        FROM videos
        WHERE sync_dirty = 1
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (remaining,),
    )
    remaining -= len(videos)
    transcripts = await _fetch_all(
        db,
        """
        SELECT video_id, raw_text, language, source_type, created_at, updated_at, deleted_at,
               sync_dirty, sync_last_pushed_at, origin_device_id
        FROM transcripts
        WHERE sync_dirty = 1
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (remaining,),
    )
    remaining -= len(transcripts)
    articles = await _fetch_all(
        db,
        """
        SELECT video_id, title, lead, body, fact_box, timestamps, llm_provider, llm_model,
               llm_reasoning_effort, llm_generated_at, created_at, updated_at, deleted_at,
               sync_dirty, sync_last_pushed_at, origin_device_id
        FROM articles
        WHERE sync_dirty = 1
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (remaining,),
    )
    return {
        "categories": categories,
        "channels": channels,
        "videos": videos,
        "transcripts": transcripts,
        "articles": articles,
    }


async def _fetch_all(
    db: aiosqlite.Connection,
    sql: str,
    params: Sequence[object] = (),
) -> list[dict[str, Any]]:
    cursor = await db.execute(sql, tuple(params))
    return _rows_to_dicts(await cursor.fetchall())


async def mark_rows_pushed(
    db: aiosqlite.Connection,
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    pushed_at_sql = SYNC_NOW_SQL
    for table in REMOTE_SYNC_TABLES:
        key = REMOTE_SYNC_KEY_COLUMNS[table]
        for row in rows_by_table.get(table, ()):
            await db.execute(
                f"""
                UPDATE {table}
                SET sync_dirty = 0,
                    sync_last_pushed_at = {pushed_at_sql}
                WHERE {key} = ?
                  AND updated_at = ?
                """,
                (row.get(key), row.get("updated_at")),
            )
    await db.commit()


async def apply_remote_rows(
    db: aiosqlite.Connection,
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, int]:
    handlers = {
        "categories": _apply_category,
        "channels": _apply_channel,
        "videos": _apply_video,
        "transcripts": _apply_transcript,
        "articles": _apply_article,
    }
    applied = {table: 0 for table in REMOTE_SYNC_TABLES}
    for table in REMOTE_SYNC_TABLES:
        handler = handlers[table]
        for row in rows_by_table.get(table, ()):
            applied[table] += await handler(db, row)
    await db.commit()
    return applied


async def _apply_category(db: aiosqlite.Connection, row: Mapping[str, Any]) -> int:
    category_uid = str(row.get("category_uid") or "").strip()
    if not category_uid:
        return 0
    local = await _fetch_one(db, "SELECT * FROM categories WHERE category_uid = ?", (category_uid,))
    if not _is_remote_newer(row, local):
        return 0
    category_name = str(row.get("name") or "").strip() or category_uid
    if row.get("deleted_at") and " [deleted:" not in category_name:
        category_name = f"{category_name} [deleted:{category_uid}]"
    await db.execute(
        """
        INSERT INTO categories(
            category_uid, name, sort_order, processing_stage, is_default, created_at,
            updated_at, deleted_at, sync_dirty, sync_last_pushed_at, origin_device_id
        )
        VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?, 0, ?, ?)
        ON CONFLICT(category_uid) DO UPDATE SET
            name=excluded.name,
            sort_order=excluded.sort_order,
            processing_stage=excluded.processing_stage,
            is_default=excluded.is_default,
            updated_at=excluded.updated_at,
            deleted_at=excluded.deleted_at,
            sync_dirty=0,
            sync_last_pushed_at=excluded.sync_last_pushed_at,
            origin_device_id=excluded.origin_device_id
        """,
        (
            category_uid,
            category_name,
            int(row.get("sort_order") or 0),
            row.get("processing_stage") or "off",
            int(row.get("is_default") or 0),
            row.get("created_at"),
            row.get("updated_at"),
            row.get("deleted_at"),
            row.get("updated_at"),
            row.get("origin_device_id") or "",
        ),
    )
    if row.get("deleted_at"):
        await _move_channels_from_deleted_category(db, category_uid)
    return 1


async def _apply_channel(db: aiosqlite.Connection, row: Mapping[str, Any]) -> int:
    channel_id = str(row.get("channel_id") or "").strip()
    if not channel_id:
        return 0
    local = await _fetch_one(db, "SELECT * FROM channels WHERE channel_id = ?", (channel_id,))
    if not _is_remote_newer(row, local):
        return 0
    category_id = await _category_id_for_uid(db, str(row.get("category_uid") or ""))
    await db.execute(
        """
        INSERT INTO channels(
            channel_id, channel_name, rss_url, is_active, category_id,
            last_seen_published_at, channel_handle, channel_url_canonical,
            channel_thumbnail_url, channel_description, channel_language_hint,
            metadata_fetched_at, metadata_fetch_status, rss_priority, created_at, updated_at,
            deleted_at, sync_dirty, sync_last_pushed_at, origin_device_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?, 0, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            channel_name=excluded.channel_name,
            rss_url=excluded.rss_url,
            is_active=excluded.is_active,
            category_id=excluded.category_id,
            last_seen_published_at=excluded.last_seen_published_at,
            channel_handle=excluded.channel_handle,
            channel_url_canonical=excluded.channel_url_canonical,
            channel_thumbnail_url=excluded.channel_thumbnail_url,
            channel_description=excluded.channel_description,
            channel_language_hint=excluded.channel_language_hint,
            metadata_fetched_at=excluded.metadata_fetched_at,
            metadata_fetch_status=excluded.metadata_fetch_status,
            rss_priority=excluded.rss_priority,
            updated_at=excluded.updated_at,
            deleted_at=excluded.deleted_at,
            sync_dirty=0,
            sync_last_pushed_at=excluded.sync_last_pushed_at,
            origin_device_id=excluded.origin_device_id
        """,
        (
            channel_id,
            row.get("channel_name") or channel_id,
            row.get("rss_url")
            or f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            int(row.get("is_active") or 0),
            category_id,
            row.get("last_seen_published_at"),
            row.get("channel_handle"),
            row.get("channel_url_canonical"),
            row.get("channel_thumbnail_url"),
            row.get("channel_description"),
            row.get("channel_language_hint"),
            row.get("metadata_fetched_at"),
            row.get("metadata_fetch_status") or "never",
            row.get("rss_priority") or "normal",
            row.get("created_at"),
            row.get("updated_at"),
            row.get("deleted_at"),
            row.get("updated_at"),
            row.get("origin_device_id") or "",
        ),
    )
    return 1


async def _apply_video(db: aiosqlite.Connection, row: Mapping[str, Any]) -> int:
    video_id = str(row.get("video_id") or "").strip()
    channel_id = str(row.get("channel_id") or "").strip()
    if not video_id or not channel_id:
        return 0
    channel = await _fetch_one(
        db, "SELECT channel_id FROM channels WHERE channel_id = ?", (channel_id,)
    )
    if channel is None:
        return 0
    local = await _fetch_one(db, "SELECT * FROM videos WHERE video_id = ?", (video_id,))
    if not _is_remote_newer(row, local):
        return 0
    await db.execute(
        """
        INSERT INTO videos(
            video_id, channel_id, title, upload_time, created_at, updated_at, deleted_at,
            sync_dirty, sync_last_pushed_at, origin_device_id
        )
        VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?, 0, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            channel_id=excluded.channel_id,
            title=excluded.title,
            upload_time=excluded.upload_time,
            updated_at=excluded.updated_at,
            deleted_at=excluded.deleted_at,
            sync_dirty=0,
            sync_last_pushed_at=excluded.sync_last_pushed_at,
            origin_device_id=excluded.origin_device_id
        """,
        (
            video_id,
            channel_id,
            row.get("title") or video_id,
            row.get("upload_time") or row.get("created_at") or "",
            row.get("created_at"),
            row.get("updated_at"),
            row.get("deleted_at"),
            row.get("updated_at"),
            row.get("origin_device_id") or "",
        ),
    )
    return 1


async def _apply_transcript(db: aiosqlite.Connection, row: Mapping[str, Any]) -> int:
    video_id = str(row.get("video_id") or "").strip()
    if not video_id or not await _video_exists(db, video_id):
        return 0
    local = await _fetch_one(db, "SELECT * FROM transcripts WHERE video_id = ?", (video_id,))
    if not _is_remote_newer(row, local):
        return 0
    await db.execute(
        """
        INSERT INTO transcripts(
            video_id, raw_text, language, source_type, created_at, updated_at, deleted_at,
            sync_dirty, sync_last_pushed_at, origin_device_id
        )
        VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?, 0, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            raw_text=excluded.raw_text,
            language=excluded.language,
            source_type=excluded.source_type,
            updated_at=excluded.updated_at,
            deleted_at=excluded.deleted_at,
            sync_dirty=0,
            sync_last_pushed_at=excluded.sync_last_pushed_at,
            origin_device_id=excluded.origin_device_id
        """,
        (
            video_id,
            row.get("raw_text") or "",
            row.get("language"),
            row.get("source_type") or "remote",
            row.get("created_at"),
            row.get("updated_at"),
            row.get("deleted_at"),
            row.get("updated_at"),
            row.get("origin_device_id") or "",
        ),
    )
    return 1


async def _apply_article(db: aiosqlite.Connection, row: Mapping[str, Any]) -> int:
    video_id = str(row.get("video_id") or "").strip()
    if not video_id or not await _video_exists(db, video_id):
        return 0
    local = await _fetch_one(db, "SELECT * FROM articles WHERE video_id = ?", (video_id,))
    if not _is_remote_newer(row, local):
        return 0
    await db.execute(
        """
        INSERT INTO articles(
            video_id, title, lead, body, fact_box, timestamps, llm_provider, llm_model,
            llm_reasoning_effort, llm_generated_at, created_at, updated_at, deleted_at,
            sync_dirty, sync_last_pushed_at, origin_device_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')),
                COALESCE(?, datetime('now')), ?, ?, 0, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            title=excluded.title,
            lead=excluded.lead,
            body=excluded.body,
            fact_box=excluded.fact_box,
            timestamps=excluded.timestamps,
            llm_provider=excluded.llm_provider,
            llm_model=excluded.llm_model,
            llm_reasoning_effort=excluded.llm_reasoning_effort,
            llm_generated_at=excluded.llm_generated_at,
            updated_at=excluded.updated_at,
            deleted_at=excluded.deleted_at,
            sync_dirty=0,
            sync_last_pushed_at=excluded.sync_last_pushed_at,
            origin_device_id=excluded.origin_device_id
        """,
        (
            video_id,
            row.get("title") or "",
            row.get("lead") or "",
            row.get("body") or "",
            row.get("fact_box"),
            row.get("timestamps"),
            row.get("llm_provider") or "unknown",
            row.get("llm_model") or "",
            row.get("llm_reasoning_effort") or "",
            row.get("llm_generated_at"),
            row.get("created_at"),
            row.get("updated_at"),
            row.get("deleted_at"),
            row.get("updated_at"),
            row.get("origin_device_id") or "",
        ),
    )
    return 1


async def prune_local_tombstones(
    db: aiosqlite.Connection, *, retention_days: int, batch_size: int
) -> int:
    safe_days = max(1, int(retention_days))
    safe_limit = max(1, int(batch_size))
    total = 0
    for table in REMOTE_SYNC_PRUNE_ORDER:
        key = REMOTE_SYNC_KEY_COLUMNS[table]
        cursor = await db.execute(
            f"""
            DELETE FROM {table}
            WHERE {key} IN (
                SELECT {key}
                FROM {table}
                WHERE deleted_at IS NOT NULL
                  AND sync_dirty = 0
                  AND datetime(deleted_at) < datetime('now', '-' || ? || ' days')
                LIMIT ?
            )
            """,
            (safe_days, safe_limit),
        )
        total += int(cursor.rowcount or 0)
    await db.commit()
    return total


async def _fetch_one(
    db: aiosqlite.Connection,
    sql: str,
    params: Sequence[object],
) -> dict[str, Any] | None:
    cursor = await db.execute(sql, tuple(params))
    row = await cursor.fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


async def _category_id_for_uid(db: aiosqlite.Connection, category_uid: str) -> int:
    cursor = await db.execute(
        """
        SELECT id
        FROM categories
        WHERE category_uid = ?
          AND deleted_at IS NULL
        LIMIT 1
        """,
        (category_uid,),
    )
    row = await cursor.fetchone()
    if row is not None:
        return int(row["id"])
    default_cursor = await db.execute("SELECT id FROM categories WHERE is_default = 1 LIMIT 1")
    default_row = await default_cursor.fetchone()
    if default_row is None:
        raise RuntimeError("default category not found")
    return int(default_row["id"])


async def _move_channels_from_deleted_category(
    db: aiosqlite.Connection,
    category_uid: str,
) -> None:
    cursor = await db.execute(
        "SELECT id FROM categories WHERE category_uid = ? LIMIT 1",
        (category_uid,),
    )
    row = await cursor.fetchone()
    if row is None:
        return
    deleted_category_id = int(row["id"])
    default_category_id = await _category_id_for_uid(db, "default")
    if deleted_category_id == default_category_id:
        return
    await db.execute(
        f"""
        UPDATE channels
        SET category_id = ?,
            {sync_dirty_set_clause()}
        WHERE category_id = ?
          AND deleted_at IS NULL
        """,
        (default_category_id, deleted_category_id),
    )


async def _video_exists(db: aiosqlite.Connection, video_id: str) -> bool:
    row = await _fetch_one(db, "SELECT video_id FROM videos WHERE video_id = ?", (video_id,))
    return row is not None
