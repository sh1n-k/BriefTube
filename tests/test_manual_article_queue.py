from __future__ import annotations

import asyncio

from app.database import init_database, open_database
from app.repositories import pipeline as pipeline_repo
from tests.helpers.db_seed import (
    fetch_pipeline_status,
    seed_channel,
    seed_video,
    seed_video_job,
)

CHANNEL_ID = "UCmanual001"


async def _open(tmp_path, name: str):
    db = await open_database(str(tmp_path / name))
    await init_database(db)
    await seed_channel(db, channel_id=CHANNEL_ID, channel_name="Manual Channel")
    return db


async def _add_transcript(db, video_id: str) -> None:
    await db.execute(
        """
        INSERT INTO transcripts(video_id, raw_text, language, source_type)
        VALUES (?, 'transcript', 'ko', 'manual')
        """,
        (video_id,),
    )
    await db.commit()


async def _add_article(db, video_id: str) -> None:
    await db.execute(
        "INSERT INTO articles(video_id, title, lead, body) VALUES (?, 't', 'l', 'b')",
        (video_id,),
    )
    await db.commit()


async def _jobs(db, video_id: str) -> list[dict]:
    cursor = await db.execute(
        """
        SELECT kind, status, origin, attempt_count, error_code
        FROM video_jobs
        WHERE video_id = ?
        ORDER BY id
        """,
        (video_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def _target(db, video_id: str) -> str:
    cursor = await db.execute("SELECT target_stage FROM videos WHERE video_id = ?", (video_id,))
    row = await cursor.fetchone()
    return str(row["target_stage"])


def test_enqueue_manual_article_jobs_summary(tmp_path) -> None:
    async def _run() -> dict:
        db = await _open(tmp_path, "manual-summary.db")
        try:
            # transcript_failed: no transcript, failed transcript job.
            await seed_video(
                db, video_id="vid-retry", channel_id=CHANNEL_ID, target_stage="transcript"
            )
            await seed_video_job(
                db, "vid-retry", "transcript", status="failed", error_code="non_retryable_failure"
            )
            # transcript_pending: active transcript job → skip.
            await seed_video(db, video_id="vid-active", channel_id=CHANNEL_ID)
            await seed_video_job(db, "vid-active", "transcript")
            # done: article exists → skip.
            await seed_video(db, video_id="vid-done", channel_id=CHANNEL_ID)
            await _add_transcript(db, "vid-done")
            await _add_article(db, "vid-done")

            result = await pipeline_repo.enqueue_manual_article_jobs(
                db,
                ["vid-retry", "vid-retry", "vid-active", "vid-done", "vid-missing", ""],
            )
            return {
                "result": result,
                "retry_jobs": await _jobs(db, "vid-retry"),
                "retry_target": await _target(db, "vid-retry"),
                "retry_status": await fetch_pipeline_status(db, "vid-retry"),
                "active_jobs": await _jobs(db, "vid-active"),
                "done_jobs": await _jobs(db, "vid-done"),
            }
        finally:
            await db.close()

    out = asyncio.run(_run())
    result = out["result"]
    assert result["retry"] == ["vid-retry"]
    assert result["new"] == []
    assert result["skip"] == ["vid-retry", "vid-active", "vid-done"]
    assert result["failed"] == ["vid-missing", ""]
    assert (result["new_count"], result["retry_count"], result["skip_count"]) == (0, 1, 3)
    assert result["failed_count"] == 2
    # No transcript yet: a manual transcript job opens and the target is raised to article.
    assert out["retry_target"] == "article"
    assert out["retry_jobs"][-1]["kind"] == "transcript"
    assert out["retry_jobs"][-1]["status"] == "queued"
    assert out["retry_jobs"][-1]["origin"] == "manual"
    assert out["retry_status"] == "transcript_pending"
    assert len(out["active_jobs"]) == 1
    assert out["done_jobs"] == []


def test_enqueue_manual_article_jobs_queues_article_when_transcript_exists(tmp_path) -> None:
    async def _run() -> tuple[dict, list[dict], str, str | None]:
        db = await _open(tmp_path, "manual-article.db")
        try:
            await seed_video(
                db, video_id="vid-has-transcript", channel_id=CHANNEL_ID, target_stage="transcript"
            )
            await _add_transcript(db, "vid-has-transcript")
            result = await pipeline_repo.enqueue_manual_article_jobs(db, ["vid-has-transcript"])
            return (
                result,
                await _jobs(db, "vid-has-transcript"),
                await _target(db, "vid-has-transcript"),
                await fetch_pipeline_status(db, "vid-has-transcript"),
            )
        finally:
            await db.close()

    result, jobs, target, status = asyncio.run(_run())
    # No article attempt existed before, so the request counts as new.
    assert result["new"] == ["vid-has-transcript"]
    assert jobs == [
        {
            "kind": "article",
            "status": "queued",
            "origin": "manual",
            "attempt_count": 0,
            "error_code": None,
        }
    ]
    assert target == "article"
    assert status == "llm_pending"


def test_enqueue_manual_article_jobs_resets_retrying_article_job(tmp_path) -> None:
    async def _run() -> tuple[dict, list[dict], str | None]:
        db = await _open(tmp_path, "manual-llm-failed.db")
        try:
            await seed_video(db, video_id="vid-llm-failed", channel_id=CHANNEL_ID)
            await _add_transcript(db, "vid-llm-failed")
            await seed_video_job(
                db,
                "vid-llm-failed",
                "article",
                attempt_count=2,
                error_code="llm_provider_timeout",
            )
            result = await pipeline_repo.enqueue_manual_article_jobs(db, ["vid-llm-failed"])
            return (
                result,
                await _jobs(db, "vid-llm-failed"),
                await fetch_pipeline_status(db, "vid-llm-failed"),
            )
        finally:
            await db.close()

    result, jobs, status = asyncio.run(_run())
    assert result["retry"] == ["vid-llm-failed"]
    assert result["skip_count"] == 0
    assert jobs == [
        {
            "kind": "article",
            "status": "queued",
            "origin": "manual",
            "attempt_count": 0,
            "error_code": None,
        }
    ]
    assert status == "llm_pending"


def test_enqueue_manual_article_jobs_skips_running_and_pending_article(tmp_path) -> None:
    async def _run() -> tuple[dict, list[dict], list[dict]]:
        db = await _open(tmp_path, "manual-active-article.db")
        try:
            for video_id, status in (("vid-llm-pending", "queued"), ("vid-llm-running", "running")):
                await seed_video(db, video_id=video_id, channel_id=CHANNEL_ID)
                await _add_transcript(db, video_id)
                await seed_video_job(db, video_id, "article", status=status)
            result = await pipeline_repo.enqueue_manual_article_jobs(
                db, ["vid-llm-pending", "vid-llm-running"]
            )
            return (
                result,
                await _jobs(db, "vid-llm-pending"),
                await _jobs(db, "vid-llm-running"),
            )
        finally:
            await db.close()

    result, pending_jobs, running_jobs = asyncio.run(_run())
    assert result["skip"] == ["vid-llm-pending", "vid-llm-running"]
    assert result["retry_count"] == 0
    assert len(pending_jobs) == 1
    assert pending_jobs[0]["origin"] == "auto"
    assert len(running_jobs) == 1
