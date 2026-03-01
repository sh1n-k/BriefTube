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
            "UCmanual001",
            "Manual Channel",
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCmanual001",
        ),
    )
    await db.commit()


async def _insert_video(db, *, video_id: str, pipeline_status: str) -> None:
    await db.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (video_id, "UCmanual001", video_id, "2026-02-26T00:00:00+00:00", pipeline_status),
    )
    await db.commit()


def test_enqueue_manual_article_jobs_summary(tmp_path) -> None:
    db_path = tmp_path / "manual-enqueue.db"

    async def _run() -> tuple[dict[str, object], list[tuple[str, str]]]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-retry-001", pipeline_status="transcript_failed")
        await _insert_video(db, video_id="vid-skip-001", pipeline_status="llm_pending")
        await _insert_video(db, video_id="vid-new-001", pipeline_status="archived")
        await _insert_video(db, video_id="vid-dup-001", pipeline_status="llm_failed")
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            ("vid-dup-001",),
        )
        await db.commit()

        summary = await repository.enqueue_manual_article_jobs(
            db,
            [
                "vid-retry-001",
                "vid-skip-001",
                "vid-new-001",
                "vid-dup-001",
                "",
                "vid-missing-001",
            ],
        )
        cursor = await db.execute(
            """
            SELECT video_id, status
            FROM manual_article_jobs
            ORDER BY id ASC
            """
        )
        rows = [(str(row["video_id"]), str(row["status"])) for row in await cursor.fetchall()]
        await db.close()
        return summary, rows

    summary, rows = asyncio.run(_run())
    assert summary["new"] == ["vid-new-001"]
    assert summary["retry"] == ["vid-retry-001"]
    assert set(summary["skip"]) == {"vid-skip-001", "vid-dup-001"}
    assert set(summary["failed"]) == {"", "vid-missing-001"}
    assert summary["new_count"] == 1
    assert summary["retry_count"] == 1
    assert summary["skip_count"] == 2
    assert summary["failed_count"] == 2
    assert rows == [
        ("vid-dup-001", "pending"),
        ("vid-retry-001", "pending"),
        ("vid-new-001", "pending"),
    ]


def test_manual_article_claim_mark_and_recover(tmp_path) -> None:
    db_path = tmp_path / "manual-claim.db"

    async def _run() -> tuple[dict[str, object] | None, str, int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-claim-001", pipeline_status="transcript_failed")
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            ("vid-claim-001",),
        )
        await db.commit()

        claimed = await repository.claim_next_manual_article_job(db)
        assert claimed is not None
        assert claimed["status"] == "running"
        updated = await repository.mark_manual_article_job_succeeded(db, job_id=int(claimed["id"]))
        assert updated == 1

        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            ("vid-claim-001",),
        )
        await db.commit()
        claimed_again = await repository.claim_next_manual_article_job(db)
        assert claimed_again is not None
        await db.execute(
            """
            UPDATE manual_article_jobs
            SET
                started_at = datetime('now', '-2 hours'),
                updated_at = datetime('now', '-2 hours')
            WHERE id = ?
            """,
            (int(claimed_again["id"]),),
        )
        await db.commit()
        recovered = await repository.recover_stuck_manual_article_jobs(
            db,
            stale_after_seconds=3600,
        )
        row = await repository.get_manual_article_job(db, int(claimed_again["id"]))
        assert row is not None
        status = str(row["status"])
        await db.close()
        return claimed, status, recovered

    claimed, status, recovered = asyncio.run(_run())
    assert claimed is not None
    assert status == "failed"
    assert recovered == 1


def test_recover_stuck_manual_article_jobs_recovers_only_stale_running(tmp_path) -> None:
    db_path = tmp_path / "manual-recover-threshold.db"

    async def _run() -> tuple[int, dict[str, tuple[str, str]]]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-stale-001", pipeline_status="transcript_failed")
        await _insert_video(db, video_id="vid-fresh-001", pipeline_status="transcript_failed")
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, started_at, updated_at)
            VALUES (
                ?,
                'running',
                datetime('now', '-4 hours'),
                datetime('now', '-4 hours'),
                datetime('now', '-4 hours')
            )
            """,
            ("vid-stale-001",),
        )
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, started_at, updated_at)
            VALUES (?, 'running', datetime('now'), datetime('now'), datetime('now'))
            """,
            ("vid-fresh-001",),
        )
        await db.commit()

        recovered = await repository.recover_stuck_manual_article_jobs(
            db,
            stale_after_seconds=3600,
        )
        cursor = await db.execute(
            """
            SELECT video_id, status, error_message
            FROM manual_article_jobs
            WHERE video_id IN (?, ?)
            ORDER BY id ASC
            """,
            ("vid-stale-001", "vid-fresh-001"),
        )
        rows = await cursor.fetchall()
        await db.close()
        return recovered, {str(row["video_id"]): (str(row["status"]), str(row["error_message"] or "")) for row in rows}

    recovered, statuses = asyncio.run(_run())
    assert recovered == 1
    assert statuses["vid-stale-001"][0] == "failed"
    assert "stale timeout exceeded" in statuses["vid-stale-001"][1]
    assert statuses["vid-fresh-001"][0] == "running"
    assert statuses["vid-fresh-001"][1] == ""


class _NeverCalledTranscriptService:
    async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
        raise AssertionError(f"fetch_transcript must not run: {video_id}/{preferred_language}")


class _FailingTranscriptService:
    async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
        raise RuntimeError("manual fetch failed")


async def _wait_for_job_status(db, *, job_id: int, expected: str) -> None:
    for _ in range(80):
        row = await repository.get_manual_article_job(db, job_id)
        if row and str(row.get("status")) == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach status={expected}")


def test_manual_article_worker_reuses_existing_transcript(tmp_path) -> None:
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
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            ("vid-reuse-001",),
        )
        await db.commit()

        cursor = await db.execute("SELECT MAX(id) AS max_id FROM manual_article_jobs")
        row = await cursor.fetchone()
        job_id = int(row["max_id"])

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


def test_manual_article_worker_marks_failed_on_fetch_error(tmp_path) -> None:
    db_path = tmp_path / "manual-worker-fail.db"

    async def _run() -> tuple[str, str, str]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-fail-001", pipeline_status="transcript_failed")
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            ("vid-fail-001",),
        )
        await db.commit()
        cursor = await db.execute("SELECT MAX(id) AS max_id FROM manual_article_jobs")
        row = await cursor.fetchone()
        job_id = int(row["max_id"])

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
                SELECT transcript_last_error
                FROM videos
                WHERE video_id = ?
                """,
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
