from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx

from app.config import AppConfig
from app.database import open_database
from app.services.rss import RSSParseError
from app.services.yt_dlp_feed import YtDlpFeedError, YtDlpFeedService
from app.workers.poller import poll_once


def _raise_http_404() -> None:
    request = httpx.Request("GET", "https://www.youtube.com/feeds/videos.xml")
    response = httpx.Response(404, request=request)
    raise httpx.HTTPStatusError("404", request=request, response=response)


def test_poll_once_deactivates_404_channel_after_streak(client) -> None:
    """404가 deactivate_threshold(기본 3)회 연속 후에만 비활성화."""
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UC404resilience001",
                "404 Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UC404resilience001",
            ),
        )
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCokresilience001",
                "OK Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCokresilience001",
            ),
        )
        conn.commit()

    class FakeRSSService:
        async def fetch_channel_feed(
            self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
        ):
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

    async def _run() -> tuple[int, int, int, int]:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                started_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
            # 1회차: streak=1, 아직 활성
            await poll_once(state)  # type: ignore[arg-type]
            cursor = await db.execute(
                "SELECT is_active, rss_fail_streak FROM channels WHERE channel_id = ?",
                ("UC404resilience001",),
            )
            row1 = await cursor.fetchone()

            # 2회차: streak=2, 아직 활성
            await poll_once(state)  # type: ignore[arg-type]
            cursor = await db.execute(
                "SELECT is_active, rss_fail_streak FROM channels WHERE channel_id = ?",
                ("UC404resilience001",),
            )
            row2 = await cursor.fetchone()

            # 3회차: streak=3 → 비활성화
            await poll_once(state)  # type: ignore[arg-type]
            cursor = await db.execute(
                """
                SELECT is_active, rss_fail_streak
                FROM channels
                WHERE channel_id = ?
                """,
                ("UC404resilience001",),
            )
            row3 = await cursor.fetchone()

            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM videos WHERE channel_id = ?",
                ("UCokresilience001",),
            )
            video_row = await cursor.fetchone()
            assert row1 is not None
            assert row2 is not None
            assert row3 is not None
            assert video_row is not None
            return (
                int(row1["is_active"]),
                int(row2["is_active"]),
                int(row3["is_active"]),
                int(video_row["cnt"]),
            )
        finally:
            await db.close()

    active_after_1, active_after_2, active_after_3, video_count = asyncio.run(_run())
    assert active_after_1 == 1, "Should still be active after 1 failure"
    assert active_after_2 == 1, "Should still be active after 2 failures"
    assert active_after_3 == 0, "Should be deactivated after 3 failures"
    assert video_count >= 1, "OK channel should still have videos inserted"


def test_rss_fail_streak_resets_on_success(client) -> None:
    """성공 시 fail streak이 0으로 리셋."""
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active, rss_fail_streak)
            VALUES (?, ?, ?, 1, 2)
            """,
            (
                "UCstreakrst001",
                "Streak Reset Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCstreakrst001",
            ),
        )
        conn.commit()

    class FakeRSSService:
        async def fetch_channel_feed(
            self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
        ):
            return ([], None, None)

    async def _run() -> int:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                started_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
            await poll_once(state)  # type: ignore[arg-type]
            cursor = await db.execute(
                "SELECT rss_fail_streak FROM channels WHERE channel_id = ?",
                ("UCstreakrst001",),
            )
            row = await cursor.fetchone()
            assert row is not None
            return int(row["rss_fail_streak"])
        finally:
            await db.close()

    streak = asyncio.run(_run())
    assert streak == 0, "Fail streak should reset to 0 after successful poll"


def test_rss_then_yt_dlp_does_not_call_fallback_when_rss_succeeds(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCrssok001",
                "RSS OK Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCrssok001",
            ),
        )
        conn.commit()

    fallback_calls: list[str] = []

    class FakeRSSService:
        async def fetch_channel_feed(
            self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
        ):
            return (
                [
                    {
                        "video_id": "vid-rss-ok",
                        "title": "RSS OK video",
                        "published": "2026-02-25T00:00:00+00:00",
                        "thumbnail_url": "",
                    }
                ],
                None,
                None,
            )

    class FakeYtDlpService:
        async def fetch_channel_feed(self, channel_id: str):
            fallback_calls.append(channel_id)
            return []

    async def _run() -> int:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                config=AppConfig(rss_fetcher_mode="rss_then_yt_dlp"),
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                yt_dlp_service=FakeYtDlpService(),
                started_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
            return await poll_once(state)  # type: ignore[arg-type]
        finally:
            await db.close()

    assert asyncio.run(_run()) == 1
    assert fallback_calls == []


def test_rss_then_yt_dlp_inserts_longform_after_rss_404(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(
                channel_id,
                channel_name,
                rss_url,
                is_active,
                rss_fail_streak
            )
            VALUES (?, ?, ?, 1, 2)
            """,
            (
                "UCfallback001",
                "Fallback Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCfallback001",
            ),
        )
        conn.commit()

    class FakeRSSService:
        async def fetch_channel_feed(
            self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
        ):
            _raise_http_404()

    class FakeYtDlpService:
        async def fetch_channel_feed(self, channel_id: str):
            return [
                {
                    "video_id": "vid-fallback-longform",
                    "title": "Fallback longform",
                    "published": "2026-02-25T00:00:00+00:00",
                    "thumbnail_url": "",
                }
            ]

    async def _run() -> tuple[int, int, int, int, str | None]:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                config=AppConfig(rss_fetcher_mode="rss_then_yt_dlp"),
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                yt_dlp_service=FakeYtDlpService(),
                started_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
            inserted = await poll_once(state)  # type: ignore[arg-type]
            cursor = await db.execute(
                """
                SELECT is_active, rss_fail_streak, last_seen_published_at
                FROM channels
                WHERE channel_id = ?
                """,
                ("UCfallback001",),
            )
            channel_row = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM videos WHERE video_id = ?",
                ("vid-fallback-longform",),
            )
            video_row = await cursor.fetchone()
            assert channel_row is not None
            assert video_row is not None
            return (
                inserted,
                int(channel_row["is_active"]),
                int(channel_row["rss_fail_streak"]),
                int(video_row["cnt"]),
                channel_row["last_seen_published_at"],
            )
        finally:
            await db.close()

    inserted, is_active, streak, video_count, watermark = asyncio.run(_run())
    assert inserted == 1
    assert is_active == 1
    assert streak == 0
    assert video_count == 1
    assert watermark == "2026-02-25T00:00:00+00:00"


def test_rss_then_yt_dlp_suppresses_404_deactivation_when_fallback_fails(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(
                channel_id,
                channel_name,
                rss_url,
                is_active,
                rss_fail_streak
            )
            VALUES (?, ?, ?, 1, 2)
            """,
            (
                "UCfallbackfail001",
                "Fallback Fail Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCfallbackfail001",
            ),
        )
        conn.commit()

    class FakeRSSService:
        async def fetch_channel_feed(
            self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
        ):
            _raise_http_404()

    class FakeYtDlpService:
        async def fetch_channel_feed(self, channel_id: str):
            raise YtDlpFeedError("yt-dlp failed")

    async def _run() -> tuple[int, int, int, int]:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                config=AppConfig(rss_fetcher_mode="rss_then_yt_dlp"),
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                yt_dlp_service=FakeYtDlpService(),
                started_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
            inserted = await poll_once(state)  # type: ignore[arg-type]
            cursor = await db.execute(
                "SELECT is_active, rss_fail_streak FROM channels WHERE channel_id = ?",
                ("UCfallbackfail001",),
            )
            channel_row = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM videos WHERE channel_id = ?",
                ("UCfallbackfail001",),
            )
            video_row = await cursor.fetchone()
            assert channel_row is not None
            assert video_row is not None
            return (
                inserted,
                int(channel_row["is_active"]),
                int(channel_row["rss_fail_streak"]),
                int(video_row["cnt"]),
            )
        finally:
            await db.close()

    inserted, is_active, streak, video_count = asyncio.run(_run())
    assert inserted == 0
    assert is_active == 1
    assert streak == 2
    assert video_count == 0


def test_yt_dlp_feed_service_filters_shorts_and_short_videos() -> None:
    captured_command: list[str] = []
    payloads = [
        {
            "id": "long001",
            "title": "Long video",
            "webpage_url": "https://www.youtube.com/watch?v=long001",
            "duration": 300,
            "timestamp": 1779890400,
            "thumbnail": "https://example.test/thumb.jpg",
        },
        {
            "id": "shorts001",
            "title": "Shorts video",
            "webpage_url": "https://www.youtube.com/shorts/shorts001",
            "duration": 999,
            "timestamp": 1779890401,
        },
        {
            "id": "shortduration001",
            "title": "Short duration video",
            "webpage_url": "https://www.youtube.com/watch?v=shortduration001",
            "duration": 120,
            "timestamp": 1779890402,
        },
    ]

    async def fake_runner(command: list[str], timeout_seconds: float):
        captured_command.extend(command)
        return 0, "\n".join(json.dumps(payload) for payload in payloads), ""

    async def _run() -> list[dict[str, str]]:
        service = YtDlpFeedService(
            playlist_limit=7,
            timeout_seconds=9,
            longform_min_seconds=180,
            runner=fake_runner,
        )
        return await service.fetch_channel_feed("UCyt001")

    entries = asyncio.run(_run())
    assert [entry["video_id"] for entry in entries] == ["long001"]
    assert entries[0]["title"] == "Long video"
    assert entries[0]["published"] == "2026-05-27T14:00:00+00:00"
    assert entries[0]["thumbnail_url"] == "https://example.test/thumb.jpg"
    assert "https://www.youtube.com/channel/UCyt001/videos" in captured_command
    assert "--playlist-end" in captured_command
    assert "7" in captured_command


def test_yt_dlp_feed_service_raises_when_error_output_has_no_entries() -> None:
    async def fake_runner(command: list[str], timeout_seconds: float):
        return 0, "", "ERROR: extractor failed"

    async def _run() -> str:
        service = YtDlpFeedService(runner=fake_runner)
        try:
            await service.fetch_channel_feed("UCytfail001")
        except YtDlpFeedError as exc:
            return str(exc)
        return ""

    assert asyncio.run(_run()) == "ERROR: extractor failed"


def test_poll_once_applies_bootstrap_lookback_for_new_channels(client) -> None:
    db_path = os.environ["DB_PATH"]
    started_at = datetime(2026, 2, 25, 0, 0, 0, tzinfo=UTC)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UClookback001",
                "Lookback Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UClookback001",
            ),
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
        async def fetch_channel_feed(
            self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
        ):
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
            old_row = await (
                await db.execute("SELECT 1 FROM videos WHERE video_id = 'vid-lookback-old'")
            ).fetchone()
            recent_row = await (
                await db.execute("SELECT 1 FROM videos WHERE video_id = 'vid-lookback-recent'")
            ).fetchone()
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
                (
                    cid,
                    f"Delay Channel {idx}",
                    f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}",
                ),
            )
        conn.commit()

    call_times: list[float] = []

    class FakeRSSService:
        async def fetch_channel_feed(
            self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
        ):
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
                started_at=datetime(2026, 3, 1, tzinfo=UTC),
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
        async def fetch_channel_feed(
            self, channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
        ):
            raise RSSParseError("RSS response XML parse failed")

    async def _run() -> tuple[int, int, bool]:
        db = await open_database(db_path)
        try:
            state = SimpleNamespace(
                db=db,
                rss_cache={},
                rss_service=FakeRSSService(),
                started_at=datetime.now(UTC),
            )
            inserted = await poll_once(state)  # type: ignore[arg-type]
            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM videos WHERE channel_id = ?",
                ("UCparseerror001",),
            )
            row = await cursor.fetchone()
            assert row is not None
            return inserted, int(row["cnt"] or 0), "UCparseerror001" in state.rss_cache
        finally:
            await db.close()

    inserted, video_count, cached = asyncio.run(_run())
    assert inserted == 0
    assert video_count == 0
    assert cached is False
