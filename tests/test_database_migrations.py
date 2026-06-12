from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.database import init_database, open_database


def test_legacy_video_rebuild_preserves_remote_sync_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-sync-video.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                rss_url TEXT NOT NULL
            );
            INSERT INTO channels(channel_id, channel_name, rss_url)
            VALUES ('UClegacysync001', 'Legacy Sync', 'https://example.test/rss');
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                thumbnail_path TEXT,
                transcript_status TEXT,
                restructure_status TEXT,
                created_at TEXT,
                viewed_at TEXT,
                updated_at TEXT,
                deleted_at TEXT,
                sync_dirty INTEGER NOT NULL DEFAULT 1,
                sync_last_pushed_at TEXT,
                origin_device_id TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO videos(
                video_id, channel_id, title, upload_time, thumbnail_path,
                transcript_status, restructure_status, created_at, viewed_at,
                updated_at, deleted_at, sync_dirty, sync_last_pushed_at, origin_device_id
            )
            VALUES (
                'vid-legacy-sync-001', 'UClegacysync001', 'Legacy', '2026-06-01T00:00:00+00:00',
                NULL, 'done', 'done', '2026-06-01T00:00:00.000Z', NULL,
                '2026-06-01T00:00:01.000Z', '2026-06-02T00:00:00.000Z', 0,
                '2026-06-01T00:00:02.000Z', 'device-a'
            );
            """
        )

    async def _run() -> dict[str, object]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            cursor = await db.execute(
                """
                SELECT pipeline_status, updated_at, deleted_at, sync_dirty,
                       sync_last_pushed_at, origin_device_id
                FROM videos
                WHERE video_id = 'vid-legacy-sync-001'
                """
            )
            row = await cursor.fetchone()
            assert row is not None
            return {key: row[key] for key in row.keys()}
        finally:
            await db.close()

    assert asyncio.run(_run()) == {
        "pipeline_status": "done",
        "updated_at": "2026-06-01T00:00:01.000Z",
        "deleted_at": "2026-06-02T00:00:00.000Z",
        "sync_dirty": 0,
        "sync_last_pushed_at": "2026-06-01T00:00:02.000Z",
        "origin_device_id": "device-a",
    }


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
                rss_last_polled_at TEXT,
                deleted_at TEXT
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
