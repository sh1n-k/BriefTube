from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.config import AppConfig
from app.repositories import remote_sync as local_sync_repo

logger = logging.getLogger(__name__)

REMOTE_SYNC_SCHEMA_VERSION = "1"


class RemoteSyncError(Exception):
    code = "remote_sync_error"


class RemoteSyncSchemaMismatch(RemoteSyncError):
    code = "schema_mismatch"


@dataclass(slots=True)
class RemoteSyncGateway:
    dsn: str
    connect_timeout_seconds: int

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.dsn, timeout=self.connect_timeout_seconds)

    async def ensure_schema(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(REMOTE_SYNC_DDL)
            version = await conn.fetchval(
                "SELECT value FROM sync_metadata WHERE key = 'schema_version'"
            )
            if str(version or "") != REMOTE_SYNC_SCHEMA_VERSION:
                raise RemoteSyncSchemaMismatch()
        finally:
            await conn.close()

    async def fetch_all(self, *, batch_size: int) -> dict[str, list[dict[str, Any]]]:
        del batch_size
        conn = await self._connect()
        try:
            return {
                "categories": await _fetch_dicts(
                    conn,
                    "SELECT * FROM sync_categories ORDER BY updated_at ASC",
                ),
                "channels": await _fetch_dicts(
                    conn,
                    "SELECT * FROM sync_channels ORDER BY updated_at ASC",
                ),
                "videos": await _fetch_dicts(
                    conn,
                    "SELECT * FROM sync_videos ORDER BY updated_at ASC",
                ),
                "transcripts": await _fetch_dicts(
                    conn,
                    "SELECT * FROM sync_transcripts ORDER BY updated_at ASC",
                ),
                "articles": await _fetch_dicts(
                    conn,
                    "SELECT * FROM sync_articles ORDER BY updated_at ASC",
                ),
            }
        finally:
            await conn.close()

    async def push(self, rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        conn = await self._connect()
        try:
            async with conn.transaction():
                for row in rows_by_table.get("categories", ()):
                    await conn.execute(UPSERT_CATEGORY, *_category_args(row))
                for row in rows_by_table.get("channels", ()):
                    await conn.execute(UPSERT_CHANNEL, *_channel_args(row))
                for row in rows_by_table.get("videos", ()):
                    await conn.execute(UPSERT_VIDEO, *_video_args(row))
                for row in rows_by_table.get("transcripts", ()):
                    await conn.execute(UPSERT_TRANSCRIPT, *_transcript_args(row))
                for row in rows_by_table.get("articles", ()):
                    await conn.execute(UPSERT_ARTICLE, *_article_args(row))
        finally:
            await conn.close()

    async def prune(self, *, retention_days: int, batch_size: int) -> int:
        conn = await self._connect()
        try:
            total = 0
            for table, key in (
                ("sync_articles", "video_id"),
                ("sync_transcripts", "video_id"),
                ("sync_videos", "video_id"),
                ("sync_channels", "channel_id"),
                ("sync_categories", "category_uid"),
            ):
                tag = await conn.execute(
                    f"""
                    DELETE FROM {table}
                    WHERE {key} IN (
                        SELECT {key}
                        FROM {table}
                        WHERE deleted_at IS NOT NULL
                          AND NULLIF(deleted_at, '')::timestamptz < now() - ($1::int * interval '1 day')
                        LIMIT $2
                    )
                    """,
                    retention_days,
                    batch_size,
                )
                total += _parse_delete_count(tag)
            await conn.execute(
                """
                INSERT INTO sync_metadata(key, value, updated_at)
                VALUES ('last_maintenance_at', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'), now() AT TIME ZONE 'utc')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """
            )
            return total
        finally:
            await conn.close()


async def run_startup_pull(config: AppConfig, db: Any) -> None:
    if not config.remote_sync_enabled:
        await local_sync_repo.set_runtime_enabled(db, False)
        return
    await local_sync_repo.set_runtime_enabled(db, True)
    gateway = RemoteSyncGateway(
        dsn=config.remote_sync_dsn,
        connect_timeout_seconds=config.remote_sync_connect_timeout_seconds,
    )
    try:
        await gateway.ensure_schema()
        rows = await gateway.fetch_all(batch_size=config.remote_sync_batch_size)
        await local_sync_repo.apply_remote_rows(db, rows)
        await local_sync_repo.record_status(db, success=True, schema_version_status="ok")
        logger.info(
            "event=remote_sync.startup_pull_succeeded",
            extra={"event": "remote_sync.startup_pull_succeeded"},
        )
    except RemoteSyncSchemaMismatch:
        await local_sync_repo.set_runtime_enabled(db, False)
        await local_sync_repo.record_status(
            db,
            success=False,
            failure_code="schema_mismatch",
            schema_version_status="mismatch",
        )
        logger.warning(
            "event=remote_sync.startup_pull_disabled code=schema_mismatch",
            extra={"event": "remote_sync.startup_pull_disabled", "code": "schema_mismatch"},
        )
    except Exception as exc:
        await local_sync_repo.set_runtime_enabled(db, False)
        await local_sync_repo.record_status(
            db,
            success=False,
            failure_code=_failure_code(exc),
            schema_version_status="unknown",
        )
        logger.warning(
            "event=remote_sync.startup_pull_failed error_type=%s",
            exc.__class__.__name__,
            extra={"event": "remote_sync.startup_pull_failed", "code": _failure_code(exc)},
        )


async def run_push_once(config: AppConfig, db: Any) -> None:
    if not config.remote_sync_enabled:
        return
    gateway = RemoteSyncGateway(
        dsn=config.remote_sync_dsn,
        connect_timeout_seconds=config.remote_sync_connect_timeout_seconds,
    )
    try:
        await gateway.ensure_schema()
        await local_sync_repo.set_runtime_enabled(db, True)
        rows = await local_sync_repo.list_dirty_rows(db, batch_size=config.remote_sync_batch_size)
        if any(rows.values()):
            await gateway.push(rows)
            await local_sync_repo.mark_rows_pushed(db, rows)
        await gateway.prune(
            retention_days=config.remote_sync_tombstone_retention_days,
            batch_size=config.remote_sync_batch_size,
        )
        await local_sync_repo.prune_local_tombstones(
            db,
            retention_days=config.remote_sync_tombstone_retention_days,
            batch_size=config.remote_sync_batch_size,
        )
        await local_sync_repo.record_status(db, success=True, schema_version_status="ok")
        logger.info(
            "event=remote_sync.push_succeeded", extra={"event": "remote_sync.push_succeeded"}
        )
    except RemoteSyncSchemaMismatch:
        await local_sync_repo.set_runtime_enabled(db, False)
        await local_sync_repo.record_status(
            db,
            success=False,
            failure_code="schema_mismatch",
            schema_version_status="mismatch",
        )
        logger.warning(
            "event=remote_sync.push_disabled code=schema_mismatch",
            extra={"event": "remote_sync.push_disabled", "code": "schema_mismatch"},
        )
    except Exception as exc:
        await local_sync_repo.record_status(
            db,
            success=False,
            failure_code=_failure_code(exc),
            schema_version_status="unknown",
        )
        logger.warning(
            "event=remote_sync.push_failed error_type=%s",
            exc.__class__.__name__,
            extra={"event": "remote_sync.push_failed", "code": _failure_code(exc)},
        )


async def run_push_loop(config: AppConfig, db: Any) -> None:
    if not config.remote_sync_enabled:
        return
    while True:
        await asyncio.sleep(config.remote_sync_push_interval_seconds)
        await run_push_once(config, db)


async def _fetch_dicts(
    conn: asyncpg.Connection,
    query: str,
    *args: object,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(query, *args)
    return [dict(row) for row in rows]


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError | OSError):
        return "connection_failed"
    if isinstance(exc, asyncpg.InvalidAuthorizationSpecificationError):
        return "auth_failed"
    if isinstance(exc, asyncpg.PostgresError):
        return "postgres_error"
    return "remote_sync_error"


def _parse_delete_count(tag: str) -> int:
    parts = str(tag or "").split()
    if len(parts) == 2 and parts[0].upper() == "DELETE":
        try:
            return int(parts[1])
        except ValueError:
            return 0
    return 0


def _category_args(row: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        row.get("category_uid"),
        row.get("name"),
        int(row.get("sort_order") or 0),
        row.get("processing_stage"),
        int(row.get("is_default") or 0),
        row.get("created_at"),
        row.get("updated_at"),
        row.get("deleted_at"),
        row.get("origin_device_id") or "",
    )


def _channel_args(row: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        row.get("channel_id"),
        row.get("channel_name"),
        row.get("rss_url"),
        int(row.get("is_active") or 0),
        row.get("category_uid"),
        row.get("last_seen_published_at"),
        row.get("channel_handle"),
        row.get("channel_url_canonical"),
        row.get("channel_thumbnail_url"),
        row.get("channel_description"),
        row.get("channel_language_hint"),
        row.get("metadata_fetched_at"),
        row.get("metadata_fetch_status"),
        row.get("created_at"),
        row.get("updated_at"),
        row.get("deleted_at"),
        row.get("origin_device_id") or "",
    )


def _video_args(row: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        row.get("video_id"),
        row.get("channel_id"),
        row.get("title"),
        row.get("upload_time"),
        row.get("created_at"),
        row.get("updated_at"),
        row.get("deleted_at"),
        row.get("origin_device_id") or "",
    )


def _transcript_args(row: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        row.get("video_id"),
        row.get("raw_text"),
        row.get("language"),
        row.get("source_type"),
        row.get("created_at"),
        row.get("updated_at"),
        row.get("deleted_at"),
        row.get("origin_device_id") or "",
    )


def _article_args(row: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        row.get("video_id"),
        row.get("title"),
        row.get("lead"),
        row.get("body"),
        row.get("fact_box"),
        row.get("timestamps"),
        row.get("llm_provider"),
        row.get("llm_model"),
        row.get("llm_reasoning_effort"),
        row.get("llm_generated_at"),
        row.get("created_at"),
        row.get("updated_at"),
        row.get("deleted_at"),
        row.get("origin_device_id") or "",
    )


REMOTE_SYNC_DDL = f"""
CREATE TABLE IF NOT EXISTS sync_metadata (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_at timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

INSERT INTO sync_metadata(key, value)
VALUES ('schema_version', '{REMOTE_SYNC_SCHEMA_VERSION}')
ON CONFLICT(key) DO NOTHING;

CREATE TABLE IF NOT EXISTS sync_categories (
    category_uid text PRIMARY KEY,
    name text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    processing_stage text NOT NULL DEFAULT 'off',
    is_default integer NOT NULL DEFAULT 0,
    created_at text,
    updated_at text NOT NULL,
    deleted_at text,
    origin_device_id text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_channels (
    channel_id text PRIMARY KEY,
    channel_name text NOT NULL,
    rss_url text NOT NULL,
    is_active integer NOT NULL DEFAULT 1,
    category_uid text,
    last_seen_published_at text,
    channel_handle text,
    channel_url_canonical text,
    channel_thumbnail_url text,
    channel_description text,
    channel_language_hint text,
    metadata_fetched_at text,
    metadata_fetch_status text NOT NULL DEFAULT 'never',
    created_at text,
    updated_at text NOT NULL,
    deleted_at text,
    origin_device_id text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_videos (
    video_id text PRIMARY KEY,
    channel_id text NOT NULL,
    title text NOT NULL,
    upload_time text NOT NULL,
    created_at text,
    updated_at text NOT NULL,
    deleted_at text,
    origin_device_id text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_transcripts (
    video_id text PRIMARY KEY,
    raw_text text NOT NULL,
    language text,
    source_type text NOT NULL,
    created_at text,
    updated_at text NOT NULL,
    deleted_at text,
    origin_device_id text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_articles (
    video_id text PRIMARY KEY,
    title text NOT NULL,
    lead text NOT NULL,
    body text NOT NULL,
    fact_box text,
    timestamps text,
    llm_provider text NOT NULL DEFAULT 'unknown',
    llm_model text NOT NULL DEFAULT '',
    llm_reasoning_effort text NOT NULL DEFAULT '',
    llm_generated_at text,
    created_at text,
    updated_at text NOT NULL,
    deleted_at text,
    origin_device_id text NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sync_categories_updated ON sync_categories(updated_at);
CREATE INDEX IF NOT EXISTS idx_sync_channels_updated ON sync_channels(updated_at);
CREATE INDEX IF NOT EXISTS idx_sync_videos_updated ON sync_videos(updated_at);
CREATE INDEX IF NOT EXISTS idx_sync_transcripts_updated ON sync_transcripts(updated_at);
CREATE INDEX IF NOT EXISTS idx_sync_articles_updated ON sync_articles(updated_at);
"""

LWW_WHERE = """
WHERE {table}.updated_at < excluded.updated_at
   OR (
        {table}.updated_at = excluded.updated_at
        AND {table}.deleted_at IS NULL
        AND excluded.deleted_at IS NOT NULL
   )
   OR (
        {table}.updated_at = excluded.updated_at
        AND COALESCE({table}.deleted_at, '') = COALESCE(excluded.deleted_at, '')
        AND {table}.origin_device_id < excluded.origin_device_id
   )
"""

UPSERT_CATEGORY = """
    INSERT INTO sync_categories(
        category_uid, name, sort_order, processing_stage, is_default, created_at,
        updated_at, deleted_at, origin_device_id
    )
    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
    ON CONFLICT(category_uid) DO UPDATE SET
        name=excluded.name,
        sort_order=excluded.sort_order,
        processing_stage=excluded.processing_stage,
        is_default=excluded.is_default,
        created_at=COALESCE(sync_categories.created_at, excluded.created_at),
        updated_at=excluded.updated_at,
        deleted_at=excluded.deleted_at,
        origin_device_id=excluded.origin_device_id
    """ + LWW_WHERE.format(table="sync_categories")

UPSERT_CHANNEL = """
    INSERT INTO sync_channels(
        channel_id, channel_name, rss_url, is_active, category_uid, last_seen_published_at,
        channel_handle, channel_url_canonical, channel_thumbnail_url, channel_description,
        channel_language_hint, metadata_fetched_at, metadata_fetch_status, created_at,
        updated_at, deleted_at, origin_device_id
    )
    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
    ON CONFLICT(channel_id) DO UPDATE SET
        channel_name=excluded.channel_name,
        rss_url=excluded.rss_url,
        is_active=excluded.is_active,
        category_uid=excluded.category_uid,
        last_seen_published_at=excluded.last_seen_published_at,
        channel_handle=excluded.channel_handle,
        channel_url_canonical=excluded.channel_url_canonical,
        channel_thumbnail_url=excluded.channel_thumbnail_url,
        channel_description=excluded.channel_description,
        channel_language_hint=excluded.channel_language_hint,
        metadata_fetched_at=excluded.metadata_fetched_at,
        metadata_fetch_status=excluded.metadata_fetch_status,
        created_at=COALESCE(sync_channels.created_at, excluded.created_at),
        updated_at=excluded.updated_at,
        deleted_at=excluded.deleted_at,
        origin_device_id=excluded.origin_device_id
    """ + LWW_WHERE.format(table="sync_channels")

UPSERT_VIDEO = """
    INSERT INTO sync_videos(video_id, channel_id, title, upload_time, created_at, updated_at, deleted_at, origin_device_id)
    VALUES($1,$2,$3,$4,$5,$6,$7,$8)
    ON CONFLICT(video_id) DO UPDATE SET
        channel_id=excluded.channel_id,
        title=excluded.title,
        upload_time=excluded.upload_time,
        created_at=COALESCE(sync_videos.created_at, excluded.created_at),
        updated_at=excluded.updated_at,
        deleted_at=excluded.deleted_at,
        origin_device_id=excluded.origin_device_id
    """ + LWW_WHERE.format(table="sync_videos")

UPSERT_TRANSCRIPT = """
    INSERT INTO sync_transcripts(video_id, raw_text, language, source_type, created_at, updated_at, deleted_at, origin_device_id)
    VALUES($1,$2,$3,$4,$5,$6,$7,$8)
    ON CONFLICT(video_id) DO UPDATE SET
        raw_text=excluded.raw_text,
        language=excluded.language,
        source_type=excluded.source_type,
        created_at=COALESCE(sync_transcripts.created_at, excluded.created_at),
        updated_at=excluded.updated_at,
        deleted_at=excluded.deleted_at,
        origin_device_id=excluded.origin_device_id
    """ + LWW_WHERE.format(table="sync_transcripts")

UPSERT_ARTICLE = """
    INSERT INTO sync_articles(
        video_id, title, lead, body, fact_box, timestamps, llm_provider, llm_model,
        llm_reasoning_effort, llm_generated_at, created_at, updated_at, deleted_at,
        origin_device_id
    )
    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
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
        created_at=COALESCE(sync_articles.created_at, excluded.created_at),
        updated_at=excluded.updated_at,
        deleted_at=excluded.deleted_at,
        origin_device_id=excluded.origin_device_id
    """ + LWW_WHERE.format(table="sync_articles")
