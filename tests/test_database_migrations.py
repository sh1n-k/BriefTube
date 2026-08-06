from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from app.database import database_transaction, init_database, open_database
from app.database_migrations import CURRENT_SCHEMA_VERSION, get_schema_version


def test_init_database_records_current_schema_version(tmp_path: Path) -> None:
    async def _run() -> tuple[int, int]:
        db = await open_database(str(tmp_path / "schema-version.db"))
        try:
            await init_database(db)
            first = await get_schema_version(db)
            await init_database(db)
            return first, await get_schema_version(db)
        finally:
            await db.close()

    assert asyncio.run(_run()) == (CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION)


def test_database_transaction_rolls_back_all_statements(tmp_path: Path) -> None:
    async def _run() -> int:
        db_path = str(tmp_path / "transaction.db")
        db = await open_database(db_path)
        try:
            await init_database(db)
        finally:
            await db.close()

        try:
            async with database_transaction(db_path) as transaction_db:
                await transaction_db.execute(
                    "INSERT INTO channels(channel_id, channel_name, rss_url) VALUES (?, ?, ?)",
                    ("UCrollback", "Rollback", "https://example.test/rss"),
                )
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass

        verify_db = await open_database(db_path)
        try:
            cursor = await verify_db.execute(
                "SELECT COUNT(*) FROM channels WHERE channel_id = ?", ("UCrollback",)
            )
            row = await cursor.fetchone()
            return int(row[0])
        finally:
            await verify_db.close()

    assert asyncio.run(_run()) == 0


def test_init_database_rejects_newer_schema_version(tmp_path: Path) -> None:
    async def _run() -> None:
        db = await open_database(str(tmp_path / "future-schema.db"))
        try:
            await db.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
            await db.commit()
            with pytest.raises(RuntimeError, match="newer than supported"):
                await init_database(db)
        finally:
            await db.close()

    asyncio.run(_run())


def test_v2_migration_drops_sync_and_tombstone_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-sync-drop.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT
            );
            INSERT INTO app_settings(key, value) VALUES ('remote_sync_device_id', 'device-a');
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_uid TEXT UNIQUE,
                name TEXT NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                llm_enabled INTEGER NOT NULL DEFAULT 1,
                processing_stage TEXT NOT NULL DEFAULT 'off',
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                deleted_at TEXT,
                sync_dirty INTEGER NOT NULL DEFAULT 1,
                sync_last_pushed_at TEXT,
                origin_device_id TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO categories(category_uid, name, sort_order, is_default, deleted_at, sync_dirty, origin_device_id)
            VALUES ('default', '미분류', 0, 1, NULL, 0, 'device-a');
            INSERT INTO categories(category_uid, name, sort_order, is_default, deleted_at, sync_dirty, origin_device_id)
            VALUES ('gone', 'Gone', 1, 0, '2026-01-01T00:00:00.000Z', 1, 'device-a');
            CREATE TABLE channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                category_id INTEGER,
                rss_next_poll_at TEXT,
                rss_priority TEXT NOT NULL DEFAULT 'normal',
                deleted_at TEXT,
                sync_dirty INTEGER NOT NULL DEFAULT 1,
                sync_last_pushed_at TEXT,
                origin_device_id TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO channels(channel_id, channel_name, rss_url, category_id, deleted_at, sync_dirty, origin_device_id)
            VALUES ('UClive001', 'Live', 'https://example.test/live', 1, NULL, 0, 'device-a');
            INSERT INTO channels(channel_id, channel_name, rss_url, category_id, deleted_at, sync_dirty, origin_device_id)
            VALUES ('UCgome001', 'GoneCh', 'https://example.test/gone', 1, '2026-01-02T00:00:00.000Z', 1, 'device-a');
            CREATE INDEX idx_channels_rss_next_poll
            ON channels(is_active, rss_next_poll_at, rss_priority)
            WHERE deleted_at IS NULL;
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                pipeline_status TEXT NOT NULL DEFAULT 'transcript_pending',
                deleted_at TEXT,
                sync_dirty INTEGER NOT NULL DEFAULT 1,
                sync_last_pushed_at TEXT,
                origin_device_id TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, deleted_at, sync_dirty, origin_device_id)
            VALUES ('vid-live', 'UClive001', 'Live V', '2026-06-01T00:00:00+00:00', 'done', NULL, 0, 'device-a');
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, deleted_at, sync_dirty, origin_device_id)
            VALUES ('vid-gone', 'UClive001', 'Gone V', '2026-06-01T00:00:00+00:00', 'done', '2026-06-02T00:00:00.000Z', 1, 'device-a');
            CREATE TABLE transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL UNIQUE,
                raw_text TEXT NOT NULL,
                language TEXT,
                source_type TEXT NOT NULL,
                deleted_at TEXT,
                sync_dirty INTEGER NOT NULL DEFAULT 1,
                sync_last_pushed_at TEXT,
                origin_device_id TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                lead TEXT NOT NULL,
                body TEXT NOT NULL,
                deleted_at TEXT,
                sync_dirty INTEGER NOT NULL DEFAULT 1,
                sync_last_pushed_at TEXT,
                origin_device_id TEXT NOT NULL DEFAULT ''
            );
            PRAGMA user_version = 1;
            """
        )

    async def _run() -> dict[str, object]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            version = await get_schema_version(db)
            cols = {}
            for table in ("categories", "channels", "videos", "transcripts", "articles"):
                cursor = await db.execute(f"PRAGMA table_info({table})")
                rows = await cursor.fetchall()
                cols[table] = {str(row["name"]) for row in rows}
            live_videos = await (
                await db.execute("SELECT video_id FROM videos ORDER BY video_id")
            ).fetchall()
            live_channels = await (
                await db.execute("SELECT channel_id FROM channels ORDER BY channel_id")
            ).fetchall()
            cats = await (
                await db.execute("SELECT category_uid FROM categories ORDER BY category_uid")
            ).fetchall()
            settings = await (
                await db.execute(
                    "SELECT COUNT(*) AS cnt FROM app_settings WHERE key LIKE 'remote_sync_%'"
                )
            ).fetchone()
            return {
                "version": version,
                "cols": cols,
                "videos": [str(r["video_id"]) for r in live_videos],
                "channels": [str(r["channel_id"]) for r in live_channels],
                "categories": [str(r["category_uid"]) for r in cats],
                "remote_settings": int(settings["cnt"]),
            }
        finally:
            await db.close()

    result = asyncio.run(_run())
    assert result["version"] == CURRENT_SCHEMA_VERSION
    for table, names in result["cols"].items():
        for banned in ("deleted_at", "sync_dirty", "sync_last_pushed_at", "origin_device_id"):
            assert banned not in names, f"{table}.{banned} still present"
    assert result["videos"] == ["vid-live"]
    assert result["channels"] == ["UClive001"]
    assert result["categories"] == ["default"]
    assert result["remote_settings"] == 0


def test_fresh_schema_has_no_remote_sync_columns(tmp_path: Path) -> None:
    async def _run() -> dict[str, set[str]]:
        db = await open_database(str(tmp_path / "fresh.db"))
        try:
            await init_database(db)
            out: dict[str, set[str]] = {}
            for table in ("categories", "channels", "videos", "transcripts", "articles"):
                cursor = await db.execute(f"PRAGMA table_info({table})")
                rows = await cursor.fetchall()
                out[table] = {str(row["name"]) for row in rows}
            return out
        finally:
            await db.close()

    cols = asyncio.run(_run())
    for table, names in cols.items():
        for banned in ("deleted_at", "sync_dirty", "sync_last_pushed_at", "origin_device_id"):
            assert banned not in names, f"{table}.{banned} still present"


def test_legacy_channels_gain_adaptive_rss_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-adaptive-rss.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_seen_published_at TEXT,
                rss_last_polled_at TEXT
            );
            INSERT INTO channels(
                channel_id, channel_name, rss_url, is_active,
                last_seen_published_at, rss_last_polled_at
            )
            VALUES
                (
                    'UCrecent001',
                    'Recent',
                    'https://example.test/recent',
                    1,
                    datetime('now', '-1 day'),
                    NULL
                ),
                (
                    'UCold001',
                    'Old',
                    'https://example.test/old',
                    1,
                    datetime('now', '-90 days'),
                    datetime('now', '-2 hours')
                );
            """
        )

    async def _run() -> dict[str, dict[str, object]]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            cursor = await db.execute(
                """
                SELECT channel_id, rss_priority, rss_poll_interval_seconds,
                       rss_next_poll_at, rss_last_etag, rss_last_modified,
                       rss_cache_feed_mode
                FROM channels
                ORDER BY channel_id
                """
            )
            rows = await cursor.fetchall()
            return {row["channel_id"]: {key: row[key] for key in row.keys()} for row in rows}
        finally:
            await db.close()

    rows = asyncio.run(_run())
    assert rows["UCrecent001"]["rss_priority"] == "normal"
    assert rows["UCrecent001"]["rss_poll_interval_seconds"] == 450
    assert rows["UCrecent001"]["rss_next_poll_at"] is not None
    assert rows["UCrecent001"]["rss_last_etag"] is None
    assert rows["UCrecent001"]["rss_last_modified"] is None
    assert rows["UCrecent001"]["rss_cache_feed_mode"] is None
    assert rows["UCold001"]["rss_poll_interval_seconds"] == 3600
