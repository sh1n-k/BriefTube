from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sqlite3
from types import SimpleNamespace

import httpx

from app.database import open_database
from app.workers.poller import poll_once


def test_poll_once_deactivates_404_channel_and_continues(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UC404resilience001", "404 Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UC404resilience001"),
        )
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCokresilience001", "OK Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCokresilience001"),
        )
        conn.commit()

    class FakeRSSService:
        async def fetch_channel_feed(self, channel_id: str, etag=None, last_modified=None):
            if channel_id == "UC404resilience001":
                request = httpx.Request("GET", "https://www.youtube.com/feeds/videos.xml")
                response = httpx.Response(404, request=request)
                raise httpx.HTTPStatusError("404", request=request, response=response)
            return (
                [
                    {
                        "video_id": "vid-resilience-001",
                        "title": "Resilience video",
                        "published": "2026-02-25T00:00:00+00:00",
                        "thumbnail_url": "",
                    }
                ],
                None,
                None,
            )

    async def _run() -> tuple[int, int, int]:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                started_at=datetime.now(timezone.utc),
            )
            inserted = await poll_once(state)  # type: ignore[arg-type]

            cursor = await db.execute(
                "SELECT is_active FROM channels WHERE channel_id = ?",
                ("UC404resilience001",),
            )
            inactive_row = await cursor.fetchone()

            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM videos WHERE channel_id = ?",
                ("UCokresilience001",),
            )
            video_row = await cursor.fetchone()
            return inserted, int(inactive_row["is_active"]), int(video_row["cnt"])
        finally:
            await db.close()

    inserted, inactive_flag, video_count = asyncio.run(_run())
    assert inserted == 1
    assert inactive_flag == 0
    assert video_count == 1


def test_poll_once_applies_bootstrap_lookback_for_new_channels(client) -> None:
    db_path = os.environ["DB_PATH"]
    started_at = datetime(2026, 2, 25, 0, 0, 0, tzinfo=timezone.utc)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UClookback001", "Lookback Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UClookback001"),
        )
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES ('rss_bootstrap_lookback_days', '60')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
        )
        conn.commit()

    very_old = (started_at - timedelta(days=120)).isoformat()
    recent = (started_at - timedelta(days=10)).isoformat()

    class FakeRSSService:
        async def fetch_channel_feed(self, channel_id: str, etag=None, last_modified=None):
            return (
                [
                    {
                        "video_id": "vid-lookback-old",
                        "title": "old video",
                        "published": very_old,
                        "thumbnail_url": "",
                    },
                    {
                        "video_id": "vid-lookback-recent",
                        "title": "recent video",
                        "published": recent,
                        "thumbnail_url": "",
                    },
                ],
                None,
                None,
            )

    async def _run() -> tuple[int, int, int]:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                started_at=started_at,
            )
            inserted = await poll_once(state)  # type: ignore[arg-type]
            old_row = await (await db.execute("SELECT 1 FROM videos WHERE video_id = 'vid-lookback-old'")).fetchone()
            recent_row = await (await db.execute("SELECT 1 FROM videos WHERE video_id = 'vid-lookback-recent'")).fetchone()
            return inserted, 1 if old_row else 0, 1 if recent_row else 0
        finally:
            await db.close()

    inserted, old_exists, recent_exists = asyncio.run(_run())
    assert inserted == 1
    assert old_exists == 0
    assert recent_exists == 1
