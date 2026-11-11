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


def test_v3_migration_adds_timed_text_to_legacy_transcripts(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-v2.db"

    async def _run() -> tuple[int, bool]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await db.execute("ALTER TABLE transcripts DROP COLUMN timed_text")
            await db.execute("PRAGMA user_version = 2")
            await db.commit()
            await init_database(db)
            cursor = await db.execute("PRAGMA table_info(transcripts)")
            columns = {str(row[1]) for row in await cursor.fetchall()}
            return await get_schema_version(db), "timed_text" in columns
        finally:
            await db.close()

    version, has_column = asyncio.run(_run())
    assert version == CURRENT_SCHEMA_VERSION
    assert has_column


_V3_VIDEOS_SQL = """
    CREATE TABLE videos (
        video_id            TEXT PRIMARY KEY,
        channel_id          TEXT NOT NULL REFERENCES channels(channel_id),
        title               TEXT NOT NULL,
        upload_time         TEXT NOT NULL,
        thumbnail_path      TEXT,
        pipeline_status     TEXT NOT NULL DEFAULT 'transcript_pending',
        processing_stage_snapshot TEXT NOT NULL DEFAULT 'full',
        transcript_retry_count INTEGER NOT NULL DEFAULT 0,
        transcript_next_attempt_at TEXT,
        transcript_target_language TEXT,
        transcript_last_error TEXT,
        transcript_last_error_at TEXT,
        retry_count         INTEGER NOT NULL DEFAULT 0,
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        viewed_at           TEXT,
        updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    );
    CREATE INDEX idx_videos_pipeline ON videos(pipeline_status);
    CREATE TABLE manual_article_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',
        error_message TEXT,
        requested_at TEXT NOT NULL DEFAULT (datetime('now')),
        started_at TEXT,
        finished_at TEXT,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE manual_transcript_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',
        retry_count INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        error_message TEXT,
        language TEXT,
        source_type TEXT,
        requested_at TEXT NOT NULL DEFAULT (datetime('now')),
        started_at TEXT,
        finished_at TEXT,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
"""

# (video_id, pipeline_status, processing_stage_snapshot, transcript?, article?)
_V3_ROWS = (
    ("v-t-pending", "transcript_pending", "full", False, False),
    ("v-t-processing", "transcript_processing", "transcript_only", False, False),
    ("v-t-failed", "transcript_failed", "full", False, False),
    ("v-no-sub", "no_subtitle", "full", False, False),
    ("v-l-pending", "llm_pending", "full", True, False),
    ("v-l-processing", "llm_processing", "full", True, False),
    ("v-l-failed", "llm_failed", "full", True, False),
    ("v-review", "manual_review", "full", True, False),
    ("v-review-orphan", "manual_review", "full", False, False),
    ("v-done", "done", "full", True, True),
    ("v-t-done-only", "transcript_done", "transcript_only", True, False),
    ("v-t-done-cleared", "transcript_done", "full", True, False),
    ("v-t-done-article", "transcript_done", "transcript_only", True, True),
    ("v-paused-off", "auto_paused", "off", False, False),
    ("v-paused-cleared", "auto_paused", "full", False, False),
    ("v-paused-article", "auto_paused", "off", True, True),
    ("v-manual-tx", "auto_paused", "off", False, False),
    ("v-manual-article", "no_subtitle", "transcript_only", False, False),
)


def test_v4_migration_splits_pipeline_status_into_target_stage_and_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-v3.db"

    async def _prepare() -> None:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
        finally:
            await db.close()

    asyncio.run(_prepare())
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP VIEW IF EXISTS video_pipeline_v")
        conn.execute("DROP TABLE video_jobs")
        conn.execute("DROP TABLE videos")
        conn.executescript(_V3_VIDEOS_SQL)
        conn.execute(
            "INSERT INTO channels(channel_id, channel_name, rss_url) VALUES ('UCv3', 'V3', 'x')"
        )
        for video_id, status, stage, has_transcript, has_article in _V3_ROWS:
            conn.execute(
                """
                INSERT INTO videos(
                    video_id, channel_id, title, upload_time, pipeline_status,
                    processing_stage_snapshot, transcript_retry_count, transcript_last_error,
                    retry_count
                )
                VALUES (?, 'UCv3', ?, '2026-06-01T00:00:00+00:00', ?, ?, 2, ?, 1)
                """,
                (
                    video_id,
                    video_id,
                    status,
                    stage,
                    "boom" if status in {"transcript_failed", "transcript_pending"} else None,
                ),
            )
            if has_transcript:
                conn.execute(
                    "INSERT INTO transcripts(video_id, raw_text, source_type) VALUES (?, 't', 'x')",
                    (video_id,),
                )
            if has_article:
                conn.execute(
                    "INSERT INTO articles(video_id, title, lead, body) VALUES (?, 'a', 'l', 'b')",
                    (video_id,),
                )
        conn.execute(
            "INSERT INTO manual_transcript_jobs(video_id, status, retry_count) "
            "VALUES ('v-manual-tx', 'pending', 1)"
        )
        conn.execute(
            "INSERT INTO manual_article_jobs(video_id, status) VALUES ('v-manual-article', 'running')"
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()

    async def _run() -> tuple[int, dict[str, dict], list[str], int]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            cursor = await db.execute(
                """
                SELECT p.*, v.title
                FROM video_pipeline_v p JOIN videos v ON v.video_id = p.video_id
                """
            )
            rows = {str(row["video_id"]): dict(row) for row in await cursor.fetchall()}
            tables = await db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'manual_%'"
            )
            manual_tables = [str(row[0]) for row in await tables.fetchall()]
            columns = await db.execute("PRAGMA table_info(videos)")
            legacy_columns = sum(
                1
                for row in await columns.fetchall()
                if row[1] in {"pipeline_status", "processing_stage_snapshot", "retry_count"}
            )
            return await get_schema_version(db), rows, manual_tables, legacy_columns
        finally:
            await db.close()

    version, rows, manual_tables, legacy_columns = asyncio.run(_run())
    assert version == CURRENT_SCHEMA_VERSION
    assert manual_tables == []
    assert legacy_columns == 0
    assert len(rows) == len(_V3_ROWS)

    def status(video_id: str) -> str:
        return str(rows[video_id]["pipeline_status"])

    assert status("v-t-pending") == "transcript_pending"
    assert rows["v-t-pending"]["transcript_attempt_count"] == 2
    assert rows["v-t-pending"]["transcript_error_message"] == "boom"
    assert status("v-t-processing") == "transcript_pending"
    assert rows["v-t-processing"]["target_stage"] == "transcript"
    assert status("v-t-failed") == "transcript_failed"
    assert rows["v-t-failed"]["transcript_error_message"] == "boom"
    assert status("v-no-sub") == "no_subtitle"
    assert status("v-l-pending") == "article_pending"
    assert rows["v-l-pending"]["article_attempt_count"] == 1
    assert status("v-l-processing") == "article_pending"
    assert rows["v-l-processing"]["pipeline_activity"] == "queued"
    assert status("v-l-failed") == "article_pending"
    assert rows["v-l-failed"]["pipeline_activity"] == "retry_wait"
    assert status("v-review") == "article_failed"
    assert rows["v-review"]["article_error_code"] == "retry_exhausted"
    assert status("v-review-orphan") == "article_failed"
    assert rows["v-review-orphan"]["article_error_code"] == "transcript_missing"
    assert status("v-done") == "article_ready"
    assert status("v-t-done-only") == "transcript_ready"
    assert rows["v-t-done-only"]["article_job_status"] is None
    assert status("v-t-done-cleared") == "paused"
    assert rows["v-t-done-cleared"]["article_job_status"] == "cancelled"
    # Facts win over the stale legacy status: an existing article always reads article_ready.
    assert status("v-t-done-article") == "article_ready"
    assert rows["v-t-done-article"]["target_stage"] == "article"
    assert status("v-paused-off") == "paused"
    assert rows["v-paused-off"]["target_stage"] == "none"
    assert rows["v-paused-off"]["transcript_job_status"] is None
    assert status("v-paused-cleared") == "paused"
    assert rows["v-paused-cleared"]["transcript_job_status"] == "cancelled"
    assert status("v-paused-article") == "article_ready"
    # Active manual requests become manual-origin jobs.
    assert status("v-manual-tx") == "transcript_pending"
    assert rows["v-manual-tx"]["transcript_job_origin"] == "manual"
    assert status("v-manual-article") == "transcript_pending"
    assert rows["v-manual-article"]["transcript_job_origin"] == "manual"
    assert rows["v-manual-article"]["target_stage"] == "article"
