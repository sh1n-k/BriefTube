"""Transcript guard 동시성 / RSS batch insert / poller disabled 토글 회귀 방지."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.database import init_database, open_database
from app.repositories import transcripts as transcripts_repo
from app.repositories import videos as videos_repo
from app.services.transcript_guard import (
    TranscriptBreakerState,
    claim_transcript_fetch_permit,
    read_transcript_guard,
    transcript_guard_mutation,
)
from app.state import AppState
from app.workers.poller import run_rss_poller


def _index_names(db_path: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    return {row[0] for row in rows}


def test_init_database_creates_hot_path_indexes_for_rss_and_transcript_queue(
    tmp_path: Path,
) -> None:
    """RSS poller / transcript worker hot path 인덱스가 init_database 후
    존재해야 한다. 없으면 풀스캔이 발생한다."""
    db_path = tmp_path / "hot-path-indexes.db"

    async def _run() -> None:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
        finally:
            await db.close()

    asyncio.run(_run())
    names = _index_names(str(db_path))
    for required in (
        "idx_videos_transcript_queue",
        "idx_videos_pipeline",
        "idx_channels_rss_next_poll",
    ):
        assert required in names, f"missing required index: {required}"


def test_transcript_guard_mutation_is_serialized_via_state_lock(
    tmp_path: Path,
) -> None:
    """``transcript_guard_mutation``이 ``state.transcript_guard_lock`` 안에서
    reload→mutate→save를 수행하므로 두 동시 mutator 사이의 lost update가
    발생하지 않는다."""
    state = SimpleNamespace(
        db=None,
        transcript_guard_lock=asyncio.Lock(),
    )
    db_path = tmp_path / "guard-concurrency.db"

    async def _run() -> int:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            state.db = db
            await asyncio.gather(
                _bump_hard_errors(state, iterations=50),
                _bump_hard_errors(state, iterations=50),
            )
            cursor = await db.execute(
                """
                SELECT value FROM app_settings
                WHERE key = 'transcript_guard_consecutive_hard_errors'
                """
            )
            row = await cursor.fetchone()
            return int(row["value"]) if row else -1
        finally:
            await db.close()

    final_count = asyncio.run(_run())
    assert final_count == 100, (
        f"expected 100 누적 hard errors without lost updates, got {final_count}"
    )


async def _bump_hard_errors(state: AppState, *, iterations: int) -> None:
    for _ in range(iterations):
        async with transcript_guard_mutation(state, state.db) as guard:
            guard.consecutive_hard_errors += 1


def test_transcript_guard_mutation_works_without_lock(tmp_path: Path) -> None:
    """테스트가 ``SimpleNamespace`` 등으로 ``transcript_guard_lock`` 없이
    state를 구성해도 ``transcript_guard_mutation``/``read_transcript_guard``가
    동작해야 한다. ``contextlib.nullcontext`` fallback을 회귀 방지한다."""
    db_path = tmp_path / "guard-no-lock.db"
    state = SimpleNamespace(
        db=None,
    )

    async def _run() -> tuple[int, int]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            state.db = db
            async with transcript_guard_mutation(state, db) as guard:
                guard.consecutive_hard_errors = 3
            read_back = await read_transcript_guard(state, db)
            return read_back.consecutive_hard_errors, read_back.half_open_probe_remaining
        finally:
            await db.close()

    hard_errors, probes = asyncio.run(_run())
    assert hard_errors == 3
    assert probes >= 1


def test_half_open_probe_counter_allows_zero_round_trip(tmp_path: Path) -> None:
    """half-open probe를 모두 소진한 상태는 0으로 저장/로드되어야 한다."""
    db_path = tmp_path / "guard-zero-probe.db"

    async def _run() -> int:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await transcripts_repo.save_transcript_guard_state(
                db,
                adaptive_factor=1.0,
                cooldown_until=None,
                consecutive_hard_errors=0,
                consecutive_successes=0,
                breaker_state="half_open",
                half_open_probe_remaining=0,
            )
            payload = await transcripts_repo.get_transcript_guard_state(db)
            return int(payload["half_open_probe_remaining"])
        finally:
            await db.close()

    assert asyncio.run(_run()) == 0


def test_half_open_fetch_permit_is_consumed_once_concurrently(tmp_path: Path) -> None:
    """동시에 두 worker가 half-open probe를 요청해도 잔여 probe=1이면
    하나만 fetch permit을 얻고, counter는 0으로 저장된다."""
    db_path = tmp_path / "guard-probe-permit.db"
    state = SimpleNamespace(
        db=None,
        transcript_guard_lock=asyncio.Lock(),
    )

    async def _run() -> tuple[int, int, str]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            state.db = db
            await transcripts_repo.save_transcript_guard_state(
                db,
                adaptive_factor=1.0,
                cooldown_until=None,
                consecutive_hard_errors=0,
                consecutive_successes=0,
                breaker_state="half_open",
                half_open_probe_remaining=1,
            )
            results = await asyncio.gather(
                claim_transcript_fetch_permit(
                    state,
                    db,
                    channel_id="UCpermit001",
                    half_open_probe_count=1,
                ),
                claim_transcript_fetch_permit(
                    state,
                    db,
                    channel_id="UCpermit002",
                    half_open_probe_count=1,
                ),
            )
            permits = sum(1 for permitted, _guard in results if permitted)
            guard = await read_transcript_guard(state, db)
            return permits, guard.half_open_probe_remaining, guard.breaker_state.value
        finally:
            await db.close()

    permits, probes, breaker_state = asyncio.run(_run())
    assert permits == 1
    assert probes == 0
    assert breaker_state == TranscriptBreakerState.HALF_OPEN.value


def test_transcript_guard_mutation_reloads_latest_state_under_lock(
    tmp_path: Path,
) -> None:
    """동일 lock 안에서 두 번째 ``transcript_guard_mutation`` 호출은 첫 번째
    mutation 결과를 반영한 최신 guard를 본다."""
    state = SimpleNamespace(
        db=None,
        transcript_guard_lock=asyncio.Lock(),
    )
    db_path = tmp_path / "guard-reload.db"

    async def _run() -> tuple[int, int]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            state.db = db
            async with transcript_guard_mutation(state, db) as guard:
                guard.adaptive_factor = 7.5
            async with transcript_guard_mutation(state, db) as guard:
                observed = guard.adaptive_factor
            async with transcript_guard_mutation(state, db) as guard:
                guard.adaptive_factor = 2.0
            async with transcript_guard_mutation(state, db) as guard:
                observed2 = guard.adaptive_factor
            return observed, observed2
        finally:
            await db.close()

    first, second = asyncio.run(_run())
    assert first == 7.5
    assert second == 2.0


def test_insert_videos_if_absent_batch_keeps_category_processing_stage(
    tmp_path: Path,
) -> None:
    """배치 insert는 단일 insert와 동일하게 채널의
    ``category.processing_stage`` 규칙을 따른다. ``off`` → ``auto_paused``,
    그 외 → ``transcript_pending``, 그리고 ``processing_stage_snapshot``이
    ``off|transcript_only|full``로 매핑된다."""
    db_path = tmp_path / "batch-insert.db"

    async def _seed() -> None:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await db.execute(
                "INSERT INTO categories(name, processing_stage) VALUES(?, ?)",
                ("Auto Paused Cat", "off"),
            )
            await db.execute(
                "INSERT INTO categories(name, processing_stage) VALUES(?, ?)",
                ("Full Cat", "full"),
            )
            await db.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active, category_id)
                SELECT 'UCautocat001', 'Auto Paused Channel', 'https://example.test/rss', 1, id
                FROM categories WHERE name = 'Auto Paused Cat'
                """
            )
            await db.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active, category_id)
                SELECT 'UCfullcat001', 'Full Channel', 'https://example.test/rss', 1, id
                FROM categories WHERE name = 'Full Cat'
                """
            )
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_seed())

    async def _run() -> tuple[int, dict[str, str], int]:
        db = await open_database(str(db_path))
        try:
            inserted = await videos_repo.insert_videos_if_absent_batch(
                db,
                [
                    ("vid-batch-auto-1", "UCautocat001", "auto 1", "2026-03-01T00:00:00+00:00"),
                    ("vid-batch-auto-2", "UCautocat001", "auto 2", "2026-03-01T00:00:01+00:00"),
                    ("vid-batch-full-1", "UCfullcat001", "full 1", "2026-03-01T00:00:02+00:00"),
                ],
            )
            pipeline_cursor = await db.execute(
                """
                SELECT video_id, pipeline_status, processing_stage_snapshot
                FROM videos
                WHERE video_id LIKE 'vid-batch-%'
                ORDER BY video_id
                """
            )
            pipeline_map: dict[str, str] = {}
            for row in await pipeline_cursor.fetchall():
                pipeline_map[row["video_id"]] = (
                    f"{row['pipeline_status']}|{row['processing_stage_snapshot']}"
                )
            inserted_second = await videos_repo.insert_videos_if_absent_batch(
                db,
                [
                    ("vid-batch-auto-1", "UCautocat001", "dup", "2026-03-01T00:00:00+00:00"),
                    ("vid-batch-new", "UCfullcat001", "new", "2026-03-01T00:00:09+00:00"),
                ],
            )
            count_cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM videos WHERE video_id LIKE 'vid-batch-%'"
            )
            count_row = await count_cursor.fetchone()
            return inserted, pipeline_map, int(inserted_second), int(count_row["cnt"])
        finally:
            await db.close()

    inserted, pipeline_map, second_inserted, total = asyncio.run(_run())
    assert inserted == 3
    assert second_inserted == 1
    assert total == 4
    assert pipeline_map["vid-batch-auto-1"] == "auto_paused|off"
    assert pipeline_map["vid-batch-auto-2"] == "auto_paused|off"
    assert pipeline_map["vid-batch-full-1"] == "transcript_pending|full"


def test_poller_worker_disabled_is_observed_after_wait_loop(
    tmp_path: Path,
) -> None:
    """RSS poller는 ``worker_rss_enabled``가 false이면 채널을 처리하지
    않는다. 채널별 루프 내에서는 ``is_worker_enabled``를 호출하지 않으므로
    사이클 시작 또는 wait loop의 5초 step에서 즉시 비활성화가 반영된다."""
    db_path = tmp_path / "poller-disabled.db"

    async def _seed_and_disable() -> None:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await db.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (
                    "UCpollerdisable001",
                    "Disable Channel",
                    "https://www.youtube.com/feeds/videos.xml?channel_id=UCpollerdisable001",
                ),
            )
            await db.execute(
                """
                INSERT INTO app_settings(key, value)
                VALUES('worker_rss_enabled', 'false')
                """
            )
            await db.commit()
        finally:
            await db.close()

    class _FakeService:
        async def fetch_channel_feed(
            self,
            channel_id: str,
            etag: str | None = None,
            last_modified: str | None = None,
            feed_mode: str = "long_form_only",
        ):
            raise AssertionError("fetch_channel_feed should not be called when worker is disabled")

    class _NoopYtDlp:
        pass

    class _NoopChannelResolver:
        pass

    async def _run() -> None:
        from app.config import AppConfig
        from app.state import AppState

        config = AppConfig(
            polling_interval_minutes=15,
            rss_inter_channel_delay_seconds=0,
            rss_consecutive_error_abort_threshold=999,
        )
        db = await open_database(str(db_path))
        state = AppState(
            config=config,
            db=db,
            http_client=None,  # type: ignore[arg-type]
            rss_service=_FakeService(),  # type: ignore[arg-type]
            yt_dlp_service=_NoopYtDlp(),  # type: ignore[arg-type]
            transcript_service=None,  # type: ignore[arg-type]
            channel_resolver=_NoopChannelResolver(),  # type: ignore[arg-type]
            llm_client=None,  # type: ignore[arg-type]
            llm_capability_probe=None,  # type: ignore[arg-type]
            telegram_notifier=None,  # type: ignore[arg-type]
            started_at=datetime.now(UTC),
        )
        poller_task = asyncio.create_task(run_rss_poller(state))
        await asyncio.sleep(0.6)
        cursor = await db.execute(
            "SELECT rss_last_polled_at FROM channels WHERE channel_id = ?",
            ("UCpollerdisable001",),
        )
        row = await cursor.fetchone()
        polled_at = row["rss_last_polled_at"] if row else None
        poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await poller_task
        await db.close()
        assert polled_at is None, "rss_last_polled_at should remain NULL while worker is disabled"

    asyncio.run(_seed_and_disable())
    asyncio.run(_run())


def test_poller_worker_disabled_breaks_wait_loop_after_cycle(
    tmp_path: Path,
) -> None:
    """RSS poller는 사이클 종료 후 wait loop의 5초 step에서 ``is_worker_enabled``를
    재확인한다. 채널 1개를 즉시 처리(304 응답)한 뒤 worker를 disabled로
    토글하면, 다음 wait loop step에서 break 되어야 한다.

    검증 방식: 사이클 시작 전 ``enabled=True``, 사이클 종료 직후
    ``enabled=False``로 토글 → poller의 5초 step 안에서 wait loop를 빠져나옴.
    """
    db_path = tmp_path / "poller-disabled-wait-loop.db"

    async def _seed() -> None:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await db.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active, rss_next_poll_at)
                VALUES (?, ?, ?, 1, datetime('now', '-1 hour'))
                """,
                (
                    "UCpollerwlloop001",
                    "Wait Loop Channel",
                    "https://www.youtube.com/feeds/videos.xml?channel_id=UCpollerwlloop001",
                ),
            )
            await db.execute(
                """
                INSERT INTO app_settings(key, value)
                VALUES('worker_rss_enabled', 'true')
                """
            )
            await db.commit()
        finally:
            await db.close()

    async def _toggle_off() -> None:
        db = await open_database(str(db_path))
        try:
            await db.execute(
                """
                UPDATE app_settings SET value = 'false', updated_at = datetime('now')
                WHERE key = 'worker_rss_enabled'
                """
            )
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_seed())

    class _FakeService:
        def __init__(self) -> None:
            self.call_count = 0

        async def fetch_channel_feed(
            self,
            channel_id: str,
            etag: str | None = None,
            last_modified: str | None = None,
            feed_mode: str = "long_form_only",
        ):
            self.call_count += 1
            return ([], None, None)

    async def _run() -> int:
        from app.config import AppConfig
        from app.state import AppState

        config = AppConfig(
            polling_interval_minutes=15,
            rss_inter_channel_delay_seconds=0,
            rss_consecutive_error_abort_threshold=999,
        )
        db = await open_database(str(db_path))
        state = AppState(
            config=config,
            db=db,
            http_client=None,  # type: ignore[arg-type]
            rss_service=_FakeService(),  # type: ignore[arg-type]
            yt_dlp_service=type("Noop", (), {})(),  # type: ignore[arg-type]
            transcript_service=None,  # type: ignore[arg-type]
            channel_resolver=type("Noop", (), {})(),  # type: ignore[arg-type]
            llm_client=None,  # type: ignore[arg-type]
            llm_capability_probe=None,  # type: ignore[arg-type]
            telegram_notifier=None,  # type: ignore[arg-type]
            started_at=datetime.now(UTC),
        )
        poller_task = asyncio.create_task(run_rss_poller(state))
        await asyncio.sleep(0.5)
        first_count = state.rss_service.call_count
        assert first_count == 1, "first cycle should have polled exactly one channel"
        await _toggle_off()
        await asyncio.sleep(6.5)
        second_count = state.rss_service.call_count
        poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await poller_task
        await db.close()
        return second_count

    polled = asyncio.run(_run())
    assert polled == 1, (
        "wait loop의 is_worker_enabled 재확인이 5초 step 안에 disabled를 감지해야 한다"
    )
