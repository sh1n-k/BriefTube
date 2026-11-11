from __future__ import annotations

import asyncio
import os
import sqlite3
from types import SimpleNamespace

import pytest
from requests import Response, Session

from app.database import init_database, open_database, recover_stuck_jobs
from app.repositories import llm as llm_repo
from app.repositories import pipeline as pipeline_repo
from app.repositories import settings as settings_repo
from app.services import transcript as transcript_service_module
from app.services.transcript import TranscriptFetchResult
from app.workers.transcript_worker import run_transcript_fetcher
from tests.helpers.db_seed import seed_video_job, seed_video_job_sync


def _seed_channel_sync(conn: sqlite3.Connection, channel_id: str, name: str) -> None:
    conn.execute(
        """
        INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (channel_id, name, f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"),
    )


def _seed_video_sync(
    conn: sqlite3.Connection,
    video_id: str,
    channel_id: str,
    upload_time: str,
    *,
    target_stage: str = "article",
) -> None:
    conn.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, target_stage)
        VALUES (?, ?, ?, ?, ?)
        """,
        (video_id, channel_id, video_id, upload_time, target_stage),
    )


async def _seed_channel(db, channel_id: str, name: str) -> None:
    await db.execute(
        """
        INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (channel_id, name, f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"),
    )


async def _seed_video(db, video_id: str, channel_id: str, *, target_stage: str = "article") -> None:
    await db.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, target_stage)
        VALUES (?, ?, ?, '2026-02-10T00:00:00+00:00', ?)
        """,
        (video_id, channel_id, video_id, target_stage),
    )


async def _status(db, video_id: str) -> str:
    cursor = await db.execute(
        "SELECT pipeline_status FROM video_pipeline_v WHERE video_id = ?", (video_id,)
    )
    row = await cursor.fetchone()
    return str(row["pipeline_status"])


async def _activity(db, video_id: str) -> str:
    cursor = await db.execute(
        "SELECT pipeline_activity FROM video_pipeline_v WHERE video_id = ?", (video_id,)
    )
    row = await cursor.fetchone()
    return str(row["pipeline_activity"])


async def _job(db, job_id: int) -> dict:
    cursor = await db.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,))
    row = await cursor.fetchone()
    return dict(row)


def test_list_claimable_transcript_jobs_prioritizes_recent_and_due(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCpolicy001", "Policy Channel")
        _seed_video_sync(conn, "vid-policy-old", "UCpolicy001", "2026-01-01T00:00:00+00:00")
        _seed_video_sync(conn, "vid-policy-new", "UCpolicy001", "2026-02-01T00:00:00+00:00")
        _seed_video_sync(conn, "vid-policy-future", "UCpolicy001", "2026-03-01T00:00:00+00:00")
        seed_video_job_sync(conn, "vid-policy-old", "transcript")
        seed_video_job_sync(
            conn, "vid-policy-new", "transcript", next_attempt_at="2000-01-01 00:00:00"
        )
        seed_video_job_sync(
            conn, "vid-policy-future", "transcript", next_attempt_at="2999-01-01 00:00:00"
        )
        conn.commit()

    async def _load_ids() -> list[str]:
        db = await open_database(db_path)
        try:
            rows = await pipeline_repo.list_claimable_transcript_jobs(db, limit=10)
            return [row["video_id"] for row in rows]
        finally:
            await db.close()

    ids = asyncio.run(_load_ids())
    assert ids == ["vid-policy-new", "vid-policy-old"]


def test_list_claimable_transcript_jobs_puts_manual_requests_first(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCpolicy002", "Policy Channel 2")
        _seed_video_sync(conn, "vid-prio-auto-new", "UCpolicy002", "2026-05-01T00:00:00+00:00")
        _seed_video_sync(conn, "vid-prio-manual-old", "UCpolicy002", "2026-01-01T00:00:00+00:00")
        seed_video_job_sync(conn, "vid-prio-auto-new", "transcript")
        seed_video_job_sync(conn, "vid-prio-manual-old", "transcript", origin="manual")
        conn.commit()

    async def _load_ids() -> list[str]:
        db = await open_database(db_path)
        try:
            rows = await pipeline_repo.list_claimable_transcript_jobs(db, limit=10)
            return [row["video_id"] for row in rows]
        finally:
            await db.close()

    ids = asyncio.run(_load_ids())
    assert ids[:2] == ["vid-prio-manual-old", "vid-prio-auto-new"]


def test_schedule_transcript_job_retry_updates_attempt_and_next_attempt(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCretry001", "Retry Channel")
        _seed_video_sync(conn, "vid-retry-001", "UCretry001", "2026-02-10T00:00:00+00:00")
        job_id = seed_video_job_sync(conn, "vid-retry-001", "transcript")
        conn.commit()

    async def _run() -> tuple[dict, str]:
        db = await open_database(db_path)
        try:
            await pipeline_repo.schedule_transcript_job_retry(
                db,
                job_id,
                delay_seconds=120,
                error_code="retryable_transient",
                error_message="network timeout",
            )
            return await _job(db, job_id), await _status(db, "vid-retry-001")
        finally:
            await db.close()

    job, status = asyncio.run(_run())
    assert job["status"] == "queued"
    assert job["attempt_count"] == 1
    assert job["next_attempt_at"] is not None
    assert job["error_code"] == "retryable_transient"
    assert job["error_message"] == "network timeout"
    assert status == "transcript_pending"


def test_recover_stuck_jobs_requeues_running_jobs_of_both_kinds(tmp_path) -> None:
    db_path = tmp_path / "recover-stuck-jobs.db"

    async def _run() -> tuple[int, str, str, str, str]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await _seed_channel(db, "UCrecover001", "Recover Channel")
            await _seed_video(db, "vid-recover-llm", "UCrecover001")
            await _seed_video(db, "vid-recover-transcript", "UCrecover001")
            await seed_video_job(db, "vid-recover-llm", "article", status="running")
            await seed_video_job(db, "vid-recover-transcript", "transcript", status="running")
            recovered = await recover_stuck_jobs(db)
            return (
                recovered,
                await _status(db, "vid-recover-llm"),
                await _status(db, "vid-recover-transcript"),
                await _activity(db, "vid-recover-llm"),
                await _activity(db, "vid-recover-transcript"),
            )
        finally:
            await db.close()

    recovered, llm_status, transcript_status, llm_activity, transcript_activity = asyncio.run(
        _run()
    )
    assert recovered == 2
    assert (llm_status, llm_activity) == ("article_pending", "queued")
    assert (transcript_status, transcript_activity) == ("transcript_pending", "queued")


def test_recover_running_transcript_jobs_requeues_processing_rows(tmp_path) -> None:
    db_path = tmp_path / "recover-stuck-transcript-jobs.db"

    async def _run() -> tuple[int, str]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await _seed_channel(db, "UCrecover002", "Recover Channel 2")
            await _seed_video(db, "vid-recover-transcript-only", "UCrecover002")
            await seed_video_job(db, "vid-recover-transcript-only", "transcript", status="running")
            recovered = await pipeline_repo.recover_running_transcript_jobs(db)
            return recovered, await _status(db, "vid-recover-transcript-only")
        finally:
            await db.close()

    recovered, transcript_status = asyncio.run(_run())
    assert recovered == 1
    assert transcript_status == "transcript_pending"


def _fetcher_config(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        db_path=db_path,
        transcript_fetch_batch_size=1,
        transcript_request_interval_seconds=1,
        transcript_idle_sleep_seconds=1,
        transcript_retry_base_delay_seconds=1,
        transcript_retry_max_delay_seconds=4,
        transcript_retry_max_attempts=2,
        transcript_fetch_timeout_seconds=3,
        transcript_jitter_ratio=0,
        transcript_adaptive_enabled=False,
        transcript_adaptive_max_factor=8.0,
        transcript_hard_cooldown_base_seconds=5,
        transcript_hard_cooldown_max_seconds=10,
        transcript_recovery_success_window=1,
        transcript_general_error_slowdown_multiplier=1.25,
        transcript_channel_min_interval_seconds=0,
        transcript_channel_pick_lookahead=1,
        transcript_channel_hard_cooldown_seconds=5,
        transcript_breaker_half_open_probe_count=1,
    )


def test_transcript_fetcher_recovers_processing_rows(tmp_path) -> None:
    db_path = tmp_path / "recover-processing.db"

    class _FakeTranscriptService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
            self.calls.append(video_id)
            return TranscriptFetchResult("recovered transcript", "ko", "manual", None)

    async def _run() -> tuple[str, str | None, list[str]]:
        db = await open_database(str(db_path))
        fake_service = _FakeTranscriptService()
        try:
            await init_database(db)
            await _seed_channel(db, "UCrecover003", "Recover Channel 3")
            await _seed_video(
                db, "vid-recover-processing", "UCrecover003", target_stage="transcript"
            )
            await seed_video_job(db, "vid-recover-processing", "transcript", status="running")
            state = SimpleNamespace(
                config=_fetcher_config(str(db_path)),
                db=db,
                transcript_service=fake_service,
                transcript_worker_lock=asyncio.Lock(),
                transcript_fetch_lock=asyncio.Lock(),
            )
            task = asyncio.create_task(run_transcript_fetcher(state))
            try:
                for _ in range(80):
                    cursor = await db.execute(
                        """
                        SELECT p.pipeline_status, t.raw_text
                        FROM video_pipeline_v p
                        LEFT JOIN transcripts t ON t.video_id = p.video_id
                        WHERE p.video_id = ?
                        """,
                        ("vid-recover-processing",),
                    )
                    row = await cursor.fetchone()
                    if row is not None and row["raw_text"] == "recovered transcript":
                        return str(row["pipeline_status"]), row["raw_text"], fake_service.calls
                    await asyncio.sleep(0.05)
                raise AssertionError("transcript fetcher did not recover and process row")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            await db.close()

    status, raw_text, calls = asyncio.run(_run())
    assert status == "transcript_ready"
    assert raw_text == "recovered transcript"
    assert calls == ["vid-recover-processing"]


def test_transcript_service_applies_requests_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_timeouts: list[int] = []

    def _fake_request(self, method, url, **kwargs):
        captured_timeouts.append(kwargs.get("timeout"))
        response = Response()
        response.status_code = 200
        response._content = b"ok"
        response.url = url
        return response

    monkeypatch.setattr(Session, "request", _fake_request)
    session = transcript_service_module._TimeoutSession(default_timeout_seconds=7)

    session.get("https://example.com/default-timeout")
    session.post("https://example.com/explicit-timeout", timeout=3)

    assert captured_timeouts == [7, 3]


def test_transcript_service_reapplies_configured_headers_after_api_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_headers: dict[str, str] = {}

    class _FakeFetchedTranscript:
        language_code = "ko"
        is_generated = False

        def to_raw_data(self):
            return [{"text": "hello"}]

    class _FakeYouTubeTranscriptApi:
        def __init__(self, http_client):
            self.http_client = http_client
            self.http_client.headers.update({"Accept-Language": "en-US"})

        def list(self, video_id):
            http_client = self.http_client

            class _FakeTrack:
                language_code = "ko"
                is_generated = False
                _url = "https://www.youtube.com/api/timedtext?lang=ko"

                def fetch(self):
                    observed_headers.update(dict(http_client.headers))
                    return _FakeFetchedTranscript()

            return [_FakeTrack()]

    monkeypatch.setattr(
        transcript_service_module, "YouTubeTranscriptApi", _FakeYouTubeTranscriptApi
    )
    service = transcript_service_module.TranscriptService(request_timeout_seconds=7)
    service.apply_transcript_request_headers({"Accept-Language": "ko-KR,ko;q=0.9"})

    raw_text, language, source_type, timed_text = service._fetch_transcript_sync(
        "vid-header-001", "ko"
    )

    assert raw_text == "hello"
    assert language == "ko"
    assert source_type == "manual"
    assert timed_text is None
    assert observed_headers["Accept-Language"] == "ko-KR,ko;q=0.9"


def test_complete_transcript_job_sets_language_and_queues_article(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCsave001", "Save Channel")
        _seed_video_sync(conn, "vid-save-001", "UCsave001", "2026-02-12T00:00:00+00:00")
        job_id = seed_video_job_sync(
            conn,
            "vid-save-001",
            "transcript",
            status="running",
            origin="manual",
            error_code="retryable_transient",
            error_message="old error",
        )
        conn.commit()

    async def _run() -> tuple[str, str | None, dict, dict]:
        db = await open_database(db_path)
        try:
            await pipeline_repo.complete_transcript_job(
                db,
                job_id=job_id,
                video_id="vid-save-001",
                raw_text="hello",
                language="ko",
                source_type="manual",
            )
            cursor = await db.execute(
                "SELECT transcript_target_language FROM videos WHERE video_id = ?",
                ("vid-save-001",),
            )
            row = await cursor.fetchone()
            article_cursor = await db.execute(
                "SELECT * FROM video_jobs WHERE video_id = ? AND kind = 'article'",
                ("vid-save-001",),
            )
            article_job = dict(await article_cursor.fetchone())
            return (
                await _status(db, "vid-save-001"),
                row["transcript_target_language"],
                await _job(db, job_id),
                article_job,
            )
        finally:
            await db.close()

    status, target_language, transcript_job, article_job = asyncio.run(_run())
    assert status == "article_pending"
    assert target_language == "ko"
    assert transcript_job["status"] == "succeeded"
    assert transcript_job["error_code"] is None
    assert transcript_job["error_message"] is None
    assert article_job["status"] == "queued"
    assert article_job["origin"] == "manual"


def test_complete_transcript_job_stops_at_transcript_target(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCsave002", "Save Channel 2")
        _seed_video_sync(
            conn,
            "vid-save-002",
            "UCsave002",
            "2026-02-12T00:00:00+00:00",
            target_stage="transcript",
        )
        job_id = seed_video_job_sync(conn, "vid-save-002", "transcript", status="running")
        conn.commit()

    async def _run() -> tuple[str, int]:
        db = await open_database(db_path)
        try:
            await pipeline_repo.complete_transcript_job(
                db,
                job_id=job_id,
                video_id="vid-save-002",
                raw_text="hello",
                language="ko",
                source_type="manual",
            )
            cursor = await db.execute(
                "SELECT COUNT(1) FROM video_jobs WHERE video_id = ? AND kind = 'article'",
                ("vid-save-002",),
            )
            row = await cursor.fetchone()
            return await _status(db, "vid-save-002"), int(row[0])
        finally:
            await db.close()

    status, article_jobs = asyncio.run(_run())
    assert status == "transcript_ready"
    assert article_jobs == 0


def test_list_claimable_transcript_jobs_can_avoid_last_channel(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCfair001", "Fair Channel A")
        _seed_channel_sync(conn, "UCfair002", "Fair Channel B")
        _seed_video_sync(conn, "vid-fair-a", "UCfair001", "2026-02-28T00:00:00+00:00")
        _seed_video_sync(conn, "vid-fair-b", "UCfair002", "2026-02-27T00:00:00+00:00")
        seed_video_job_sync(conn, "vid-fair-a", "transcript")
        seed_video_job_sync(conn, "vid-fair-b", "transcript")
        conn.commit()

    async def _run() -> tuple[str, str]:
        db = await open_database(db_path)
        try:
            normal = await pipeline_repo.list_claimable_transcript_jobs(db, limit=1, lookahead=10)
            avoided = await pipeline_repo.list_claimable_transcript_jobs(
                db,
                limit=1,
                lookahead=10,
                avoid_channel_id="UCfair001",
            )
            return normal[0]["video_id"], avoided[0]["video_id"]
        finally:
            await db.close()

    normal_id, avoided_id = asyncio.run(_run())
    assert normal_id == "vid-fair-a"
    assert avoided_id == "vid-fair-b"


def test_defer_channel_transcript_jobs_defers_same_channel_except_excluded(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCdefer001", "Defer Channel")
        _seed_video_sync(conn, "vid-defer-keep", "UCdefer001", "2026-02-25T00:00:00+00:00")
        _seed_video_sync(conn, "vid-defer-move", "UCdefer001", "2026-02-24T00:00:00+00:00")
        keep_job = seed_video_job_sync(conn, "vid-defer-keep", "transcript")
        move_job = seed_video_job_sync(conn, "vid-defer-move", "transcript")
        conn.commit()

    async def _run() -> tuple[str | None, str | None]:
        db = await open_database(db_path)
        try:
            await pipeline_repo.defer_channel_transcript_jobs(
                db,
                channel_id="UCdefer001",
                delay_seconds=600,
                exclude_video_id="vid-defer-keep",
            )
            keep = await _job(db, keep_job)
            move = await _job(db, move_job)
            return keep["next_attempt_at"], move["next_attempt_at"]
        finally:
            await db.close()

    keep_next_attempt_at, move_next_attempt_at = asyncio.run(_run())
    assert keep_next_attempt_at is None
    assert move_next_attempt_at is not None


def test_record_article_job_failure_skips_when_job_is_not_running(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCllm001", "LLM Channel")
        _seed_video_sync(conn, "vid-llm-001", "UCllm001", "2026-02-21T00:00:00+00:00")
        job_id = seed_video_job_sync(conn, "vid-llm-001", "article", status="succeeded")
        conn.commit()

    async def _run() -> tuple[str, int, dict]:
        db = await open_database(db_path)
        try:
            next_status, affected = await pipeline_repo.record_article_job_failure(
                db, job_id, max_attempts=3, error_code="llm_provider_timeout"
            )
            return next_status, affected, await _job(db, job_id)
        finally:
            await db.close()

    next_status, affected, job = asyncio.run(_run())
    assert next_status == "failed"
    assert affected == 0
    assert job["status"] == "succeeded"
    assert job["attempt_count"] == 0


def test_article_attempt_floor_allows_at_least_one_attempt(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCllmfloor001", "LLM Floor Channel")
        _seed_video_sync(conn, "vid-llm-floor-001", "UCllmfloor001", "2026-02-21T00:00:00+00:00")
        conn.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-llm-floor-001", "hello", "ko", "manual"),
        )
        job_id = seed_video_job_sync(conn, "vid-llm-floor-001", "article")
        conn.commit()

    async def _run() -> tuple[str | None, str, int, str, dict]:
        db = await open_database(db_path)
        try:
            candidate = await pipeline_repo.claim_next_article_job(db, max_attempts=0)
            next_status, failed_affected = await pipeline_repo.record_article_job_failure(
                db, job_id, max_attempts=0, error_code="llm_provider_timeout"
            )
            return (
                str(candidate["video_id"]) if candidate else None,
                next_status,
                failed_affected,
                await _status(db, "vid-llm-floor-001"),
                await _job(db, job_id),
            )
        finally:
            await db.close()

    candidate_id, next_status, affected, pipeline_status, job = asyncio.run(_run())
    assert candidate_id == "vid-llm-floor-001"
    assert next_status == "failed"
    assert affected == 1
    assert pipeline_status == "article_failed"
    assert job["attempt_count"] == 1
    assert job["error_code"] == "llm_provider_timeout"


def test_record_article_job_failure_requeues_until_attempts_run_out(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCllmretry001", "LLM Retry Channel")
        _seed_video_sync(conn, "vid-llm-retry-001", "UCllmretry001", "2026-02-21T00:00:00+00:00")
        job_id = seed_video_job_sync(conn, "vid-llm-retry-001", "article", status="running")
        conn.commit()

    async def _run() -> tuple[str, str, str, dict]:
        db = await open_database(db_path)
        try:
            next_status, _ = await pipeline_repo.record_article_job_failure(
                db, job_id, max_attempts=3, error_code="llm_provider_timeout"
            )
            return (
                next_status,
                await _status(db, "vid-llm-retry-001"),
                await _activity(db, "vid-llm-retry-001"),
                await _job(db, job_id),
            )
        finally:
            await db.close()

    next_status, pipeline_status, activity, job = asyncio.run(_run())
    assert next_status == "queued"
    assert (pipeline_status, activity) == ("article_pending", "retry_wait")
    assert job["attempt_count"] == 1
    assert job["error_code"] == "llm_provider_timeout"


def test_requeue_article_job_keeps_attempt_count(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCrequeue001", "Requeue Channel")
        _seed_video_sync(conn, "vid-requeue-001", "UCrequeue001", "2026-02-21T00:00:00+00:00")
        job_id = seed_video_job_sync(
            conn, "vid-requeue-001", "article", status="running", attempt_count=2
        )
        conn.commit()

    async def _run() -> tuple[int, str, dict]:
        db = await open_database(db_path)
        try:
            affected = await pipeline_repo.requeue_article_job(db, job_id)
            return affected, await _status(db, "vid-requeue-001"), await _job(db, job_id)
        finally:
            await db.close()

    affected, pipeline_status, job = asyncio.run(_run())
    assert affected == 1
    assert pipeline_status == "article_pending"
    assert job["attempt_count"] == 2


def test_claim_next_article_job_fails_jobs_without_transcript(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCorphan001", "Orphan Channel")
        _seed_video_sync(conn, "vid-orphan-pending", "UCorphan001", "2026-02-22T00:00:00+00:00")
        _seed_video_sync(conn, "vid-orphan-safe", "UCorphan001", "2026-02-22T00:00:01+00:00")
        conn.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-orphan-safe", "hello", "ko", "manual"),
        )
        orphan_job = seed_video_job_sync(conn, "vid-orphan-pending", "article")
        seed_video_job_sync(conn, "vid-orphan-safe", "article")
        conn.commit()

    async def _run() -> tuple[str | None, dict, dict[str, str]]:
        db = await open_database(db_path)
        try:
            candidate = await pipeline_repo.claim_next_article_job(db, max_attempts=3)
            return (
                str(candidate["video_id"]) if candidate else None,
                await _job(db, orphan_job),
                {
                    "vid-orphan-pending": await _status(db, "vid-orphan-pending"),
                    "vid-orphan-safe": await _status(db, "vid-orphan-safe"),
                    "vid-orphan-safe-activity": await _activity(db, "vid-orphan-safe"),
                },
            )
        finally:
            await db.close()

    candidate_id, orphan, statuses = asyncio.run(_run())
    assert candidate_id == "vid-orphan-safe"
    assert orphan["status"] == "failed"
    assert orphan["error_code"] == "transcript_missing"
    assert statuses["vid-orphan-pending"] == "article_failed"
    assert statuses["vid-orphan-safe"] == "article_pending"
    assert statuses["vid-orphan-safe-activity"] == "running"


def test_ensure_llm_config_missing_alert_is_deduplicated_and_reset(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCalert001", "Alert Channel")
        _seed_video_sync(conn, "vid-alert-001", "UCalert001", "2026-02-23T00:00:00+00:00")
        job_id = seed_video_job_sync(conn, "vid-alert-001", "article")
        conn.commit()

    async def _run() -> tuple[bool, bool, int, str | None]:
        db = await open_database(db_path)
        try:
            created_first = await llm_repo.ensure_llm_config_missing_alert(db)
            created_second = await llm_repo.ensure_llm_config_missing_alert(db)
            await db.execute("UPDATE video_jobs SET status = 'succeeded' WHERE id = ?", (job_id,))
            await db.commit()
            created_after_done = await llm_repo.ensure_llm_config_missing_alert(db)
            assert created_after_done is False

            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM system_alerts WHERE alert_type = ?",
                (llm_repo.ALERT_TYPE_LLM_CONFIG_MISSING,),
            )
            count_row = await cursor.fetchone()
            sent_key = await settings_repo.get_setting(
                db,
                key=llm_repo.LLM_CONFIG_MISSING_ALERT_SENT_KEY,
                default=None,
            )
            return created_first, created_second, int(count_row["cnt"] or 0), sent_key
        finally:
            await db.close()

    created_first, created_second, alert_count, sent_key = asyncio.run(_run())
    assert created_first is True
    assert created_second is False
    assert alert_count == 1
    assert sent_key == "0"


def test_transcript_worker_skips_when_lock_already_held(tmp_path) -> None:
    async def _run() -> None:
        lock = asyncio.Lock()
        await lock.acquire()
        try:
            state = SimpleNamespace(transcript_worker_lock=lock)
            # A second invocation must return immediately while the in-process
            # lock is held, instead of blocking or running a duplicate loop.
            await asyncio.wait_for(run_transcript_fetcher(state), timeout=2.0)
        finally:
            lock.release()

    asyncio.run(_run())


def test_queue_badge_counts_transcript_retry_wait_but_not_article_retry_wait(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCbadge001", "Badge Channel")
        _seed_video_sync(conn, "vid-badge-tx", "UCbadge001", "2026-02-21T00:00:00+00:00")
        _seed_video_sync(conn, "vid-badge-llm", "UCbadge001", "2026-02-21T00:00:01+00:00")
        conn.execute(
            "INSERT INTO transcripts(video_id, raw_text, language, source_type) "
            "VALUES ('vid-badge-llm', 'hello', 'ko', 'manual')"
        )
        seed_video_job_sync(
            conn, "vid-badge-tx", "transcript", attempt_count=1, error_code="retryable_transient"
        )
        seed_video_job_sync(
            conn, "vid-badge-llm", "article", attempt_count=1, error_code="llm_provider_timeout"
        )
        conn.commit()

    async def _run() -> dict:
        db = await open_database(db_path)
        try:
            return await pipeline_repo.queue_status(db)
        finally:
            await db.close()

    counts = asyncio.run(_run())
    assert counts["transcript_retry_wait"] == 1
    assert counts["article_retry_wait"] == 1
    assert counts["badge_count"] == 1


def test_retry_article_for_video_marks_retry_wait_job_manual(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCretryorigin", "Retry Origin Channel")
        _seed_video_sync(conn, "vid-retry-origin", "UCretryorigin", "2026-02-21T00:00:00+00:00")
        job_id = seed_video_job_sync(
            conn,
            "vid-retry-origin",
            "article",
            attempt_count=2,
            error_code="llm_provider_timeout",
        )
        conn.commit()

    async def _run() -> tuple[int, dict]:
        db = await open_database(db_path)
        try:
            retried = await pipeline_repo.retry_article_for_video(db, "vid-retry-origin")
            return retried, await _job(db, job_id)
        finally:
            await db.close()

    retried, job = asyncio.run(_run())
    assert retried == 1
    assert job["origin"] == "manual"
    assert job["attempt_count"] == 0
    assert job["error_code"] is None


def test_article_retry_skips_failed_jobs_without_transcript(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_channel_sync(conn, "UCretryorphan", "Retry Orphan Channel")
        _seed_video_sync(conn, "vid-retry-orphan", "UCretryorphan", "2026-02-21T00:00:00+00:00")
        seed_video_job_sync(
            conn, "vid-retry-orphan", "article", status="failed", error_code="transcript_missing"
        )
        conn.commit()

    async def _run() -> tuple[int, int, str]:
        db = await open_database(db_path)
        try:
            bulk = await pipeline_repo.retry_failed_jobs(db, pipeline_repo.JOB_KIND_ARTICLE)
            single = await pipeline_repo.retry_article_for_video(db, "vid-retry-orphan")
            return bulk, single, await _status(db, "vid-retry-orphan")
        finally:
            await db.close()

    bulk, single, status = asyncio.run(_run())
    assert (bulk, single) == (0, 0)
    assert status == "article_failed"
