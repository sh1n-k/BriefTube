from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import repository
from app.config import AppConfig
from app.database import init_database, open_database
from app.workers.manual_article_worker import run_manual_article_worker


async def _seed_channel(db) -> None:
    await db.execute(
        """
        INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (
            "UCmanualworker002",
            "Manual Worker Channel",
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCmanualworker002",
        ),
    )
    await db.commit()


async def _insert_video(db, *, video_id: str, pipeline_status: str) -> None:
    await db.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (video_id, "UCmanualworker002", video_id, "2026-02-26T00:00:00+00:00", pipeline_status),
    )
    await db.commit()


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


def test_manual_article_worker_fetch_failure_marks_transcript_failed_and_job_failed(tmp_path) -> None:
    db_path = tmp_path / "manual-worker-fail.db"

    async def _run() -> tuple[str, str, str]:
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
                "SELECT transcript_last_error FROM videos WHERE video_id = ?",
                ("vid-fail-001",),
            )
            error_row = await cursor.fetchone()
            error_message = str(error_row["transcript_last_error"] or "")
            return str(video["pipeline_status"]), str(job["status"]), error_message
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    pipeline_status, job_status, error_message = asyncio.run(_run())
    assert pipeline_status == "transcript_failed"
    assert job_status == "failed"
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
    assert spacing >= 0.9


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
            ) VALUES (?, 'running', datetime('now'), datetime('now'), datetime('now'))
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
