from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sqlite3
import time
from types import SimpleNamespace

import httpx

from app.database import open_database
from app.services.rss import RSSParseError
from app.workers.poller import poll_once


def test_poll_once_deactivates_404_channel_and_continues(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(
                channel_id,
                channel_name,
                rss_url,
                is_active,
                rss_consecutive_404_count,
                rss_404_first_at
            )
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (
                "UC404resilience001",
                "404 Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UC404resilience001",
                2,
                "2026-02-24T00:00:00+00:00",
            ),
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
        async def fetch_channel_feed(self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
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

    async def _run() -> tuple[int, int, int, int, int]:
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
                """
                SELECT is_active, rss_consecutive_404_count
                FROM channels
                WHERE channel_id = ?
                """,
                ("UC404resilience001",),
            )
            inactive_row = await cursor.fetchone()

            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM videos WHERE channel_id = ?",
                ("UCokresilience001",),
            )
            video_row = await cursor.fetchone()
            alert_row = await (
                await db.execute(
                    "SELECT COUNT(1) AS cnt FROM system_alerts WHERE channel_id = ? AND alert_type = ?",
                    ("UC404resilience001", "rss_channel_not_found"),
                )
            ).fetchone()
            return (
                inserted,
                int(inactive_row["is_active"]),
                int(inactive_row["rss_consecutive_404_count"]),
                int(video_row["cnt"]),
                int(alert_row["cnt"]),
            )
        finally:
            await db.close()

    inserted, inactive_flag, consecutive_count, video_count, alert_count = asyncio.run(_run())
    assert inserted == 1
    assert inactive_flag == 0
    assert consecutive_count == 3
    assert video_count == 1
    assert alert_count == 1


def test_poll_once_tracks_first_404_without_deactivating(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UC404resilience002",
                "404 Tracking Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UC404resilience002",
            ),
        )
        conn.commit()

    class FakeRSSService:
        async def fetch_channel_feed(self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
            request = httpx.Request("GET", "https://www.youtube.com/feeds/videos.xml")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("404", request=request, response=response)

    async def _run() -> tuple[int, int, int, str | None]:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                started_at=datetime.now(timezone.utc),
            )
            inserted = await poll_once(state)  # type: ignore[arg-type]
            row = await (
                await db.execute(
                    """
                    SELECT is_active, rss_consecutive_404_count, rss_404_first_at
                    FROM channels
                    WHERE channel_id = ?
                    """,
                    ("UC404resilience002",),
                )
            ).fetchone()
            return (
                inserted,
                int(row["is_active"]),
                int(row["rss_consecutive_404_count"]),
                None if row["rss_404_first_at"] is None else str(row["rss_404_first_at"]),
            )
        finally:
            await db.close()

    inserted, inactive_flag, consecutive_count, first_404_at = asyncio.run(_run())
    assert inserted == 0
    assert inactive_flag == 1
    assert consecutive_count == 1
    assert first_404_at is not None


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
        async def fetch_channel_feed(self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
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


def test_poll_once_applies_inter_channel_delay(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        for idx in range(3):
            cid = f"UCdelay{idx:03d}"
            conn.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (cid, f"Delay Channel {idx}", f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"),
            )
        conn.commit()

    call_times: list[float] = []

    class FakeRSSService:
        async def fetch_channel_feed(self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
            call_times.append(time.monotonic())
            return (
                [
                    {
                        "video_id": f"vid-delay-{channel_id}",
                        "title": f"Video from {channel_id}",
                        "published": "2026-02-25T00:00:00+00:00",
                        "thumbnail_url": "",
                    }
                ],
                None,
                None,
            )

    async def _run() -> int:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                started_at=datetime.now(timezone.utc),
            )
            return await poll_once(state, inter_channel_delay=0.5)  # type: ignore[arg-type]
        finally:
            await db.close()

    inserted = asyncio.run(_run())
    assert inserted == 3

    assert len(call_times) == 3
    for j in range(1, len(call_times)):
        gap = call_times[j] - call_times[j - 1]
        assert gap >= 0.5 * 0.7, f"Channel {j} gap {gap:.3f}s < minimum 0.35s"


def test_poll_once_does_not_cache_or_insert_on_rss_parse_error(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCparseerror001",
                "Parse Error Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCparseerror001",
            ),
        )
        conn.commit()

    class FakeRSSService:
        async def fetch_channel_feed(self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
            raise RSSParseError("RSS response XML parse failed")

    async def _run() -> tuple[int, int, bool]:
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
                "SELECT COUNT(1) AS cnt FROM videos WHERE channel_id = ?",
                ("UCparseerror001",),
            )
            row = await cursor.fetchone()
            return inserted, int(row["cnt"] or 0), "UCparseerror001" in state.rss_cache
        finally:
            await db.close()

    inserted, video_count, cached = asyncio.run(_run())
    assert inserted == 0
    assert video_count == 0
    assert cached is False
