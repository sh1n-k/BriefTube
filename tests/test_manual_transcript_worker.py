from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database import init_database, open_database
from app.repositories import manual_transcripts as manual_transcripts_repo
from app.workers import manual_transcript_worker
from app.services.transcript_guard import TranscriptErrorCategory


class _FakeTranscriptService:
    def __init__(self, result=None, exc: Exception | None = None) -> None:
        self.result = result or ("collected transcript", "ko", "manual")
        self.exc = exc
        self.calls: list[tuple[str, str | None]] = []

    async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
        self.calls.append((video_id, preferred_language))
        if self.exc is not None:
            raise self.exc
        return self.result


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        transcript_idle_sleep_seconds=1,
        transcript_request_interval_seconds=1,
        transcript_fetch_timeout_seconds=3,
        transcript_jitter_ratio=0,
        transcript_adaptive_enabled=False,
        transcript_adaptive_max_factor=8.0,
        transcript_hard_cooldown_base_seconds=30,
        transcript_hard_cooldown_max_seconds=300,
        transcript_channel_hard_cooldown_seconds=60,
        transcript_recovery_success_window=1,
        transcript_breaker_half_open_probe_count=1,
    )


async def _seed_auto_paused_video(db, video_id: str) -> None:
    await db.execute(
        """
        INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (
            "UCmanualtxworker",
            "Manual Transcript Worker",
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCmanualtxworker",
        ),
    )
    await db.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, processing_stage_snapshot)
        VALUES (?, ?, ?, ?, 'auto_paused', 'off')
        """,
        (video_id, "UCmanualtxworker", f"Worker {video_id}", "2026-03-16T00:00:00+00:00"),
    )
    await db.commit()


async def _wait_for_job_status(db, video_id: str, status: str) -> dict:
    for _ in range(40):
        cursor = await db.execute(
            """
            SELECT j.status, j.error_message, j.retry_count, v.pipeline_status, t.raw_text
            FROM manual_transcript_jobs j
            JOIN videos v ON v.video_id = j.video_id
            LEFT JOIN transcripts t ON t.video_id = j.video_id
            WHERE j.video_id = ?
            ORDER BY j.id DESC
            LIMIT 1
            """,
            (video_id,),
        )
        row = await cursor.fetchone()
        if row is not None and str(row["status"]) == status:
            return dict(row)
        await asyncio.sleep(0.05)
    raise AssertionError(f"manual transcript job did not reach {status}")


async def _run_worker_until(db, transcript_service, video_id: str, status: str) -> dict:
    state = SimpleNamespace(
        config=_config(),
        db=db,
        transcript_service=transcript_service,
        manual_transcript_wake_event=asyncio.Event(),
    )
    state.manual_transcript_wake_event.set()
    task = asyncio.create_task(manual_transcript_worker.run_manual_transcript_worker(state))
    try:
        return await asyncio.wait_for(_wait_for_job_status(db, video_id, status), timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _get_guard_state(db) -> dict:
    cursor = await db.execute(
        """
        SELECT
            value
        FROM app_settings
        WHERE key = 'transcript_guard_breaker_state'
        """
    )
    state_row = await cursor.fetchone()
    cooldown_cursor = await db.execute(
        """
        SELECT value
        FROM app_settings
        WHERE key = 'transcript_guard_cooldown_until'
        """
    )
    cooldown_row = await cooldown_cursor.fetchone()
    return {
        "breaker_state": state_row["value"] if state_row else "",
        "cooldown_until": cooldown_row["value"] if cooldown_row else "",
    }


def test_manual_transcript_worker_collects_transcript(tmp_path: Path) -> None:
    async def _run() -> dict:
        db = await open_database(str(tmp_path / "worker-success.db"))
        try:
            await init_database(db)
            await _seed_auto_paused_video(db, "vid-manual-tx-worker-001")
            result = await manual_transcripts_repo.enqueue_manual_transcript_job(
                db,
                "vid-manual-tx-worker-001",
            )
            assert result["status"] == "queued"
            return await _run_worker_until(
                db,
                _FakeTranscriptService(result=("worker transcript", "ko", "manual")),
                "vid-manual-tx-worker-001",
                "succeeded",
            )
        finally:
            await db.close()

    row = asyncio.run(_run())
    assert row["pipeline_status"] == "transcript_done"
    assert row["raw_text"] == "worker transcript"


def test_manual_transcript_worker_marks_no_subtitle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> dict:
        db = await open_database(str(tmp_path / "worker-no-subtitle.db"))
        try:
            await init_database(db)
            await _seed_auto_paused_video(db, "vid-manual-tx-worker-002")
            await manual_transcripts_repo.enqueue_manual_transcript_job(db, "vid-manual-tx-worker-002")
            monkeypatch.setattr(
                manual_transcript_worker,
                "_classify_transcript_error",
                lambda exc: TranscriptErrorCategory.NO_SUBTITLE,
            )
            return await _run_worker_until(
                db,
                _FakeTranscriptService(exc=RuntimeError("no transcript available")),
                "vid-manual-tx-worker-002",
                "failed",
            )
        finally:
            await db.close()

    row = asyncio.run(_run())
    assert row["pipeline_status"] == "no_subtitle"
    assert row["retry_count"] == 1
    assert "no transcript" in str(row["error_message"])


def test_manual_transcript_worker_records_external_failure(tmp_path: Path) -> None:
    async def _run() -> dict:
        db = await open_database(str(tmp_path / "worker-failure.db"))
        try:
            await init_database(db)
            await _seed_auto_paused_video(db, "vid-manual-tx-worker-003")
            await manual_transcripts_repo.enqueue_manual_transcript_job(db, "vid-manual-tx-worker-003")
            return await _run_worker_until(
                db,
                _FakeTranscriptService(exc=RuntimeError("upstream unavailable")),
                "vid-manual-tx-worker-003",
                "failed",
            )
        finally:
            await db.close()

    row = asyncio.run(_run())
    assert row["pipeline_status"] == "auto_paused"
    assert row["retry_count"] == 1
    assert "upstream unavailable" in str(row["error_message"])


def test_manual_transcript_worker_opens_guard_on_hard_throttle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> tuple[dict, dict]:
        db = await open_database(str(tmp_path / "worker-hard-throttle.db"))
        try:
            await init_database(db)
            await _seed_auto_paused_video(db, "vid-manual-tx-worker-004")
            await manual_transcripts_repo.enqueue_manual_transcript_job(db, "vid-manual-tx-worker-004")
            monkeypatch.setattr(
                manual_transcript_worker,
                "_classify_transcript_error",
                lambda exc: TranscriptErrorCategory.HARD_THROTTLE,
            )
            row = await _run_worker_until(
                db,
                _FakeTranscriptService(exc=RuntimeError("too many requests")),
                "vid-manual-tx-worker-004",
                "failed",
            )
            return row, await _get_guard_state(db)
        finally:
            await db.close()

    row, guard = asyncio.run(_run())
    assert row["pipeline_status"] == "auto_paused"
    assert guard["breaker_state"] == "open"
    assert guard["cooldown_until"]


def test_manual_transcript_recovery_keeps_fresh_running_job(tmp_path: Path) -> None:
    async def _run() -> tuple[int, str]:
        db = await open_database(str(tmp_path / "worker-recovery.db"))
        try:
            await init_database(db)
            await _seed_auto_paused_video(db, "vid-manual-tx-worker-005")
            await manual_transcripts_repo.enqueue_manual_transcript_job(db, "vid-manual-tx-worker-005")
            claimed = await manual_transcripts_repo.claim_next_manual_transcript_job(db)
            assert claimed is not None
            recovered = await manual_transcripts_repo.recover_stuck_manual_transcript_jobs(
                db,
                stale_after_seconds=300,
            )
            cursor = await db.execute(
                "SELECT status FROM manual_transcript_jobs WHERE id = ?",
                (int(claimed["id"]),),
            )
            row = await cursor.fetchone()
            return recovered, str(row["status"])
        finally:
            await db.close()

    recovered, status = asyncio.run(_run())
    assert recovered == 0
    assert status == "running"
