from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.repositories import manual_articles as manual_articles_repo
from app.config import AppConfig
from app.database import init_database, open_database
from app.repositories import transcripts as transcripts_repo
from app.repositories import videos as videos_repo
from tests.helpers.db_seed import seed_channel, seed_video
from app.workers.manual_article_worker import run_manual_article_worker

CHANNEL_ID = "UCmanualworker002"
repository = SimpleNamespace(
    get_manual_article_job=manual_articles_repo.get_manual_article_job,
    recover_stuck_manual_article_jobs=manual_articles_repo.recover_stuck_manual_article_jobs,
    get_video=videos_repo.get_video,
)

async def _seed_channel(db) -> None:
    await seed_channel(db, channel_id=CHANNEL_ID, channel_name="Manual Worker Channel")


async def _insert_video(db, *, video_id: str, pipeline_status: str) -> None:
    await seed_video(db, video_id=video_id, channel_id=CHANNEL_ID, pipeline_status=pipeline_status)


async def _insert_pending_job(db, *, video_id: str) -> int:
    await db.execute(
        """
        INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
        VALUES (?, 'pending', datetime('now'), datetime('now'))
        """,
        (video_id,),
    )
    await db.commit()
    cursor = await db.execute("SELECT MAX(id) AS max_id FROM manual_article_jobs")
    row = await cursor.fetchone()
    return int(row["max_id"])


async def _wait_for_job_status(db, *, job_id: int, expected: str, timeout_seconds: float = 6.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        row = await repository.get_manual_article_job(db, job_id)
        if row and str(row.get("status")) == expected:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"job {job_id} did not reach status={expected}")
        await asyncio.sleep(0.05)


class _NeverCalledTranscriptService:
    async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
        raise AssertionError(f"fetch_transcript must not run: {video_id}/{preferred_language}")


class _SuccessTranscriptService:
    async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
        return (f"Fetched {video_id}", "ko", "manual")


class _FailingTranscriptService:
    async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
        raise RuntimeError("manual fetch failed")


def test_manual_article_worker_save_transcript_failure_marks_job_failed(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "manual-worker-save-fail.db"

    async def _failing_save_transcript(*args, **kwargs):
        raise RuntimeError("save transcript failed")

    monkeypatch.setattr(transcripts_repo, "save_transcript", _failing_save_transcript)

    async def _run() -> tuple[str, str]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-save-fail-001", pipeline_status="transcript_failed")
        job_id = await _insert_pending_job(db, video_id="vid-save-fail-001")

        state = SimpleNamespace(
            db=db,
            config=AppConfig(
                transcript_idle_sleep_seconds=1,
                transcript_request_interval_seconds=1,
                transcript_fetch_timeout_seconds=1,
            ),
            transcript_service=_SuccessTranscriptService(),
            manual_article_wake_event=asyncio.Event(),
            llm_wake_event=asyncio.Event(),
        )

        task = asyncio.create_task(run_manual_article_worker(state))
        try:
            await _wait_for_job_status(db, job_id=job_id, expected="failed")
            job = await repository.get_manual_article_job(db, job_id)
            assert job is not None
            return str(job["status"]), str(job["error_message"] or "")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    job_status, error_message = asyncio.run(_run())
    assert job_status == "failed"
    assert "save transcript failed" in error_message


def test_manual_article_worker_reuses_existing_transcript_without_fetch(tmp_path) -> None:
    db_path = tmp_path / "manual-worker-reuse.db"

    async def _run() -> tuple[str, str, bool]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-reuse-001", pipeline_status="manual_review")
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-reuse-001", "already exists", "ko", "manual"),
        )
        await db.commit()
        job_id = await _insert_pending_job(db, video_id="vid-reuse-001")

        state = SimpleNamespace(
            db=db,
            config=AppConfig(
                transcript_idle_sleep_seconds=1,
                transcript_request_interval_seconds=1,
                transcript_fetch_timeout_seconds=1,
            ),
            transcript_service=_NeverCalledTranscriptService(),
            manual_article_wake_event=asyncio.Event(),
            llm_wake_event=asyncio.Event(),
        )

        task = asyncio.create_task(run_manual_article_worker(state))
        try:
            await _wait_for_job_status(db, job_id=job_id, expected="succeeded")
            video = await repository.get_video(db, "vid-reuse-001")
            job = await repository.get_manual_article_job(db, job_id)
            assert video is not None
            assert job is not None
            return str(video["pipeline_status"]), str(job["status"]), state.llm_wake_event.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    pipeline_status, job_status, wake_set = asyncio.run(_run())
    assert pipeline_status == "llm_pending"
    assert job_status == "succeeded"
    assert wake_set is True


def test_manual_article_worker_fetches_missing_transcript_and_succeeds(tmp_path) -> None:
    db_path = tmp_path / "manual-worker-fetch-success.db"

    async def _run() -> tuple[str, str, int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-fetch-001", pipeline_status="transcript_failed")
        job_id = await _insert_pending_job(db, video_id="vid-fetch-001")

        state = SimpleNamespace(
            db=db,
            config=AppConfig(
                transcript_idle_sleep_seconds=1,
                transcript_request_interval_seconds=1,
                transcript_fetch_timeout_seconds=1,
            ),
            transcript_service=_SuccessTranscriptService(),
            manual_article_wake_event=asyncio.Event(),
            llm_wake_event=asyncio.Event(),
        )

        task = asyncio.create_task(run_manual_article_worker(state))
        try:
            await _wait_for_job_status(db, job_id=job_id, expected="succeeded")
            video = await repository.get_video(db, "vid-fetch-001")
            assert video is not None
            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM transcripts WHERE video_id = ?",
                ("vid-fetch-001",),
            )
            transcript_row = await cursor.fetchone()
            transcript_count = int(transcript_row["cnt"] or 0)
            job = await repository.get_manual_article_job(db, job_id)
            assert job is not None
            return str(video["pipeline_status"]), str(job["status"]), transcript_count
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    pipeline_status, job_status, transcript_count = asyncio.run(_run())
    assert pipeline_status == "llm_pending"
    assert job_status == "succeeded"
    assert transcript_count == 1


def test_manual_article_worker_persists_guard_state_after_fetch(tmp_path) -> None:
    db_path = tmp_path / "manual-worker-guard-state.db"

    async def _run() -> tuple[str | None, str | None]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-guard-001", pipeline_status="transcript_failed")
        job_id = await _insert_pending_job(db, video_id="vid-guard-001")

        state = SimpleNamespace(
            db=db,
            config=AppConfig(
                transcript_idle_sleep_seconds=1,
                transcript_request_interval_seconds=1,
                transcript_fetch_timeout_seconds=1,
            ),
            transcript_service=_SuccessTranscriptService(),
            manual_article_wake_event=asyncio.Event(),
            llm_wake_event=asyncio.Event(),
        )

        task = asyncio.create_task(run_manual_article_worker(state))
        try:
            await _wait_for_job_status(db, job_id=job_id, expected="succeeded")
            guard_state = await transcripts_repo.get_transcript_guard_state(db)
            return (
                str(guard_state.get("last_channel_id") or "") or None,
                str(guard_state.get("last_channel_attempt_at") or "") or None,
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    last_channel_id, last_channel_attempt_at = asyncio.run(_run())
    assert last_channel_id == CHANNEL_ID
    assert last_channel_attempt_at


def test_manual_article_worker_fetch_failure_requeues_transcript_and_fails_job(tmp_path) -> None:
    db_path = tmp_path / "manual-worker-fail.db"

    async def _run() -> tuple[str, str, int, str, str]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-fail-001", pipeline_status="transcript_failed")
        job_id = await _insert_pending_job(db, video_id="vid-fail-001")

        state = SimpleNamespace(
            db=db,
            config=AppConfig(
                transcript_idle_sleep_seconds=1,
                transcript_request_interval_seconds=1,
                transcript_fetch_timeout_seconds=1,
            ),
            transcript_service=_FailingTranscriptService(),
            manual_article_wake_event=asyncio.Event(),
            llm_wake_event=asyncio.Event(),
        )

        task = asyncio.create_task(run_manual_article_worker(state))
        try:
            await _wait_for_job_status(db, job_id=job_id, expected="failed")
            video = await repository.get_video(db, "vid-fail-001")
            job = await repository.get_manual_article_job(db, job_id)
            assert video is not None
            assert job is not None
            cursor = await db.execute(
                """
                SELECT transcript_retry_count, transcript_last_error, transcript_next_attempt_at
                FROM videos
                WHERE video_id = ?
                """,
                ("vid-fail-001",),
            )
            error_row = await cursor.fetchone()
            retry_count = int(error_row["transcript_retry_count"] or 0)
            error_message = str(error_row["transcript_last_error"] or "")
            next_attempt_at = str(error_row["transcript_next_attempt_at"] or "")
            return (
                str(video["pipeline_status"]),
                str(job["status"]),
                retry_count,
                next_attempt_at,
                error_message,
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    pipeline_status, job_status, retry_count, next_attempt_at, error_message = asyncio.run(_run())
    assert pipeline_status == "transcript_pending"
    assert job_status == "failed"
    assert retry_count == 1
    assert next_attempt_at
    assert "manual fetch failed" in error_message


def test_manual_article_worker_processes_jobs_sequentially_with_spacing(tmp_path) -> None:
    db_path = tmp_path / "manual-worker-sequential.db"

    async def _run() -> tuple[list[str], int, float]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-seq-001", pipeline_status="transcript_failed")
        await _insert_video(db, video_id="vid-seq-002", pipeline_status="transcript_failed")
        job_id_1 = await _insert_pending_job(db, video_id="vid-seq-001")
        job_id_2 = await _insert_pending_job(db, video_id="vid-seq-002")

        starts: list[tuple[str, float]] = []
        active_calls = {"count": 0, "max": 0}

        class _TrackingService:
            async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
                active_calls["count"] += 1
                active_calls["max"] = max(active_calls["max"], active_calls["count"])
                starts.append((video_id, asyncio.get_running_loop().time()))
                try:
                    return (f"Fetched {video_id}", "ko", "manual")
                finally:
                    active_calls["count"] -= 1

        state = SimpleNamespace(
            db=db,
            config=AppConfig(
                transcript_idle_sleep_seconds=1,
                transcript_request_interval_seconds=1,
                transcript_fetch_timeout_seconds=1,
                transcript_jitter_ratio=0.0,
            ),
            transcript_service=_TrackingService(),
            manual_article_wake_event=asyncio.Event(),
            llm_wake_event=asyncio.Event(),
        )

        task = asyncio.create_task(run_manual_article_worker(state))
        try:
            await _wait_for_job_status(db, job_id=job_id_1, expected="succeeded", timeout_seconds=8.0)
            await _wait_for_job_status(db, job_id=job_id_2, expected="succeeded", timeout_seconds=8.0)
            assert len(starts) >= 2
            order = [starts[0][0], starts[1][0]]
            spacing = starts[1][1] - starts[0][1]
            return order, active_calls["max"], spacing
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    order, max_active, spacing = asyncio.run(_run())
    assert order == ["vid-seq-001", "vid-seq-002"]
    assert max_active == 1
    assert spacing >= 0.7


def test_recover_stuck_manual_article_jobs_marks_running_failed(tmp_path) -> None:
    db_path = tmp_path / "manual-worker-recover.db"

    async def _run() -> tuple[int, str, str]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-recover-001", pipeline_status="transcript_failed")
        await db.execute(
            """
            INSERT INTO manual_article_jobs(
                video_id,
                status,
                requested_at,
                started_at,
                updated_at
            ) VALUES (
                ?,
                'running',
                datetime('now', '-3 hours'),
                datetime('now', '-3 hours'),
                datetime('now', '-3 hours')
            )
            """,
            ("vid-recover-001",),
        )
        await db.commit()

        recovered = await repository.recover_stuck_manual_article_jobs(db)
        cursor = await db.execute(
            """
            SELECT status, error_message
            FROM manual_article_jobs
            WHERE video_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            ("vid-recover-001",),
        )
        row = await cursor.fetchone()
        await db.close()
        assert row is not None
        return recovered, str(row["status"]), str(row["error_message"] or "")

    recovered, status, error_message = asyncio.run(_run())
    assert recovered == 1
    assert status == "failed"
    assert "worker interrupted" in error_message
