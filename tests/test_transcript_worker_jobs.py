"""Transcript worker against video_jobs: manual requests share the auto queue."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database import init_database, open_database
from app.repositories import pipeline as pipeline_repo
from app.services.transcript import TranscriptFetchResult
from app.services.transcript_guard import TranscriptErrorCategory
from app.workers import transcript_worker
from tests.helpers.db_seed import seed_video_job

CHANNEL_ID = "UCtxjobs001"


class _FakeTranscriptService:
    def __init__(self, result=None, exc: Exception | None = None) -> None:
        self.result = result or TranscriptFetchResult("collected transcript", "ko", "manual", None)
        self.exc = exc
        self.calls: list[str] = []

    async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
        self.calls.append(video_id)
        if self.exc is not None:
            raise self.exc
        return self.result


def _config(*, retry_max_attempts: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        transcript_fetch_batch_size=1,
        transcript_request_interval_seconds=1,
        transcript_idle_sleep_seconds=1,
        transcript_retry_base_delay_seconds=60,
        transcript_retry_max_delay_seconds=120,
        transcript_retry_max_attempts=retry_max_attempts,
        transcript_fetch_timeout_seconds=3,
        transcript_jitter_ratio=0,
        transcript_adaptive_enabled=False,
        transcript_adaptive_max_factor=8.0,
        transcript_hard_cooldown_base_seconds=30,
        transcript_hard_cooldown_max_seconds=300,
        transcript_recovery_success_window=1,
        transcript_general_error_slowdown_multiplier=1.25,
        transcript_channel_min_interval_seconds=0,
        transcript_channel_pick_lookahead=5,
        transcript_channel_hard_cooldown_seconds=60,
        transcript_breaker_half_open_probe_count=1,
    )


async def _seed_video(db, video_id: str, *, target_stage: str, upload_time: str) -> None:
    await db.execute(
        """
        INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, 'Transcript Jobs', 'https://example.test/rss', 1)
        """,
        (CHANNEL_ID,),
    )
    await db.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, target_stage)
        VALUES (?, ?, ?, ?, ?)
        """,
        (video_id, CHANNEL_ID, video_id, upload_time, target_stage),
    )
    await db.commit()


async def _job(db, job_id: int) -> dict:
    cursor = await db.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,))
    return dict(await cursor.fetchone())


async def _state_row(db, video_id: str) -> dict:
    cursor = await db.execute("SELECT * FROM video_pipeline_v WHERE video_id = ?", (video_id,))
    return dict(await cursor.fetchone())


async def _run_worker_until(db, service, predicate, *, config=None) -> None:
    state = SimpleNamespace(
        config=config or _config(),
        db=db,
        transcript_service=service,
        transcript_worker_lock=asyncio.Lock(),
        transcript_fetch_lock=asyncio.Lock(),
        transcript_wake_event=asyncio.Event(),
    )
    task = asyncio.create_task(transcript_worker.run_transcript_fetcher(state))
    try:
        for _ in range(80):
            if await predicate():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("transcript worker did not reach the expected state")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_manual_transcript_request_is_collected_without_article_for_none_target(
    tmp_path: Path,
) -> None:
    async def _run() -> tuple[dict, dict]:
        db = await open_database(str(tmp_path / "manual-success.db"))
        try:
            await init_database(db)
            await _seed_video(
                db, "vid-txjob-001", target_stage="none", upload_time="2026-03-16T00:00:00+00:00"
            )
            result = await pipeline_repo.enqueue_manual_transcript_job(db, "vid-txjob-001")
            assert result["status"] == "queued"
            job_id = int(result["job_id"])

            async def _done() -> bool:
                return (await _job(db, job_id))["status"] == "succeeded"

            await _run_worker_until(db, _FakeTranscriptService(), _done)
            return await _job(db, job_id), await _state_row(db, "vid-txjob-001")
        finally:
            await db.close()

    job, state = asyncio.run(_run())
    assert job["origin"] == "manual"
    assert state["has_transcript"] == 1
    assert state["article_job_id"] is None
    assert state["pipeline_status"] == "transcript_done"


def test_manual_article_request_chains_transcript_then_article_job(tmp_path: Path) -> None:
    async def _run() -> tuple[dict, dict]:
        db = await open_database(str(tmp_path / "manual-article-chain.db"))
        try:
            await init_database(db)
            await _seed_video(
                db,
                "vid-txjob-002",
                target_stage="transcript",
                upload_time="2026-03-16T00:00:00+00:00",
            )
            summary = await pipeline_repo.enqueue_manual_article_jobs(db, ["vid-txjob-002"])
            assert summary["new_count"] == 1

            async def _chained() -> bool:
                return (await _state_row(db, "vid-txjob-002"))["article_job_id"] is not None

            await _run_worker_until(db, _FakeTranscriptService(), _chained)
            state = await _state_row(db, "vid-txjob-002")
            return state, await _job(db, int(state["article_job_id"]))
        finally:
            await db.close()

    state, article_job = asyncio.run(_run())
    assert state["target_stage"] == "article"
    assert state["transcript_job_status"] == "succeeded"
    assert article_job["status"] == "queued"
    assert article_job["origin"] == "manual"
    assert state["pipeline_status"] == "llm_pending"


def test_manual_requests_are_claimed_before_newer_auto_jobs(tmp_path: Path) -> None:
    async def _run() -> list[str]:
        db = await open_database(str(tmp_path / "manual-priority.db"))
        try:
            await init_database(db)
            await _seed_video(
                db, "vid-txjob-auto", target_stage="article", upload_time="2026-05-01T00:00:00Z"
            )
            await _seed_video(
                db, "vid-txjob-manual", target_stage="none", upload_time="2026-01-01T00:00:00Z"
            )
            await seed_video_job(db, "vid-txjob-auto", "transcript")
            await seed_video_job(db, "vid-txjob-manual", "transcript", origin="manual")
            service = _FakeTranscriptService()

            async def _first_done() -> bool:
                return len(service.calls) >= 1

            await _run_worker_until(db, service, _first_done)
            return service.calls
        finally:
            await db.close()

    calls = asyncio.run(_run())
    assert calls[0] == "vid-txjob-manual"


def test_no_subtitle_fails_the_job_with_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        transcript_worker,
        "_classify_transcript_error",
        lambda exc: TranscriptErrorCategory.NO_SUBTITLE,
    )

    async def _run() -> tuple[dict, dict]:
        db = await open_database(str(tmp_path / "no-subtitle.db"))
        try:
            await init_database(db)
            await _seed_video(
                db, "vid-txjob-003", target_stage="none", upload_time="2026-03-16T00:00:00Z"
            )
            job_id = await seed_video_job(db, "vid-txjob-003", "transcript", origin="manual")

            async def _failed() -> bool:
                return (await _job(db, job_id))["status"] == "failed"

            await _run_worker_until(
                db, _FakeTranscriptService(exc=RuntimeError("no transcript available")), _failed
            )
            return await _job(db, job_id), await _state_row(db, "vid-txjob-003")
        finally:
            await db.close()

    job, state = asyncio.run(_run())
    assert job["error_code"] == "no_subtitle"
    assert state["pipeline_status"] == "no_subtitle"


def test_retryable_failure_schedules_retry_for_manual_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        transcript_worker,
        "_classify_transcript_error",
        lambda exc: TranscriptErrorCategory.RETRYABLE_TRANSIENT,
    )

    async def _run() -> tuple[dict, dict]:
        db = await open_database(str(tmp_path / "retryable.db"))
        try:
            await init_database(db)
            await _seed_video(
                db, "vid-txjob-004", target_stage="none", upload_time="2026-03-16T00:00:00Z"
            )
            job_id = await seed_video_job(db, "vid-txjob-004", "transcript", origin="manual")

            async def _retry_scheduled() -> bool:
                return (await _job(db, job_id))["attempt_count"] == 1

            await _run_worker_until(
                db,
                _FakeTranscriptService(exc=RuntimeError("upstream unavailable")),
                _retry_scheduled,
            )
            return await _job(db, job_id), await _state_row(db, "vid-txjob-004")
        finally:
            await db.close()

    job, state = asyncio.run(_run())
    assert job["status"] == "queued"
    assert job["next_attempt_at"] is not None
    assert job["error_code"] == "retryable_transient"
    assert job["error_message"] == "upstream unavailable"
    assert state["pipeline_status"] == "transcript_pending"


def test_hard_throttle_opens_guard_and_defers_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        transcript_worker,
        "_classify_transcript_error",
        lambda exc: TranscriptErrorCategory.HARD_THROTTLE,
    )

    async def _run() -> tuple[dict, dict, str]:
        db = await open_database(str(tmp_path / "hard-throttle.db"))
        try:
            await init_database(db)
            await _seed_video(
                db, "vid-txjob-005", target_stage="none", upload_time="2026-03-17T00:00:00Z"
            )
            await _seed_video(
                db, "vid-txjob-006", target_stage="none", upload_time="2026-03-16T00:00:00Z"
            )
            job_id = await seed_video_job(db, "vid-txjob-005", "transcript", origin="manual")
            sibling_id = await seed_video_job(db, "vid-txjob-006", "transcript")

            async def _deferred() -> bool:
                return (await _job(db, job_id))["attempt_count"] == 1

            await _run_worker_until(
                db, _FakeTranscriptService(exc=RuntimeError("too many requests")), _deferred
            )
            cursor = await db.execute(
                "SELECT value FROM app_settings WHERE key = 'transcript_guard_breaker_state'"
            )
            breaker = await cursor.fetchone()
            return await _job(db, job_id), await _job(db, sibling_id), str(breaker["value"])
        finally:
            await db.close()

    job, sibling, breaker_state = asyncio.run(_run())
    assert job["status"] == "queued"
    assert job["error_code"] == "hard_throttle"
    assert sibling["next_attempt_at"] is not None
    assert breaker_state == "open"


def test_non_retryable_failure_fails_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        transcript_worker,
        "_classify_transcript_error",
        lambda exc: TranscriptErrorCategory.NON_RETRYABLE_FAILURE,
    )

    async def _run() -> tuple[dict, dict]:
        db = await open_database(str(tmp_path / "non-retryable.db"))
        try:
            await init_database(db)
            await _seed_video(
                db, "vid-txjob-007", target_stage="article", upload_time="2026-03-16T00:00:00Z"
            )
            job_id = await seed_video_job(db, "vid-txjob-007", "transcript")

            async def _failed() -> bool:
                return (await _job(db, job_id))["status"] == "failed"

            await _run_worker_until(
                db, _FakeTranscriptService(exc=RuntimeError("video unavailable")), _failed
            )
            return await _job(db, job_id), await _state_row(db, "vid-txjob-007")
        finally:
            await db.close()

    job, state = asyncio.run(_run())
    assert job["attempt_count"] == 1
    assert job["error_code"] == "non_retryable_failure"
    assert job["error_message"] == "video unavailable"
    assert state["pipeline_status"] == "transcript_failed"
