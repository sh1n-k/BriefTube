from __future__ import annotations

import asyncio
import os
import sqlite3
from types import SimpleNamespace

import pytest
from requests import Response, Session

from app.database import init_database, open_database, recover_stuck_jobs
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.services import transcript as transcript_service_module
from app.services.transcript_guard import _compute_retry_delay_seconds
from app.workers.transcript_worker import run_transcript_fetcher

repository = SimpleNamespace(
    ALERT_TYPE_LLM_CONFIG_MISSING=llm_repo.ALERT_TYPE_LLM_CONFIG_MISSING,
    LLM_CONFIG_MISSING_ALERT_SENT_KEY=llm_repo.LLM_CONFIG_MISSING_ALERT_SENT_KEY,
    pop_pending_transcript_videos=transcripts_repo.pop_pending_transcript_videos,
    schedule_transcript_retry=transcripts_repo.schedule_transcript_retry,
    recover_stuck_transcript_jobs=transcripts_repo.recover_stuck_transcript_jobs,
    save_transcript=transcripts_repo.save_transcript,
    defer_channel_transcript_retries=transcripts_repo.defer_channel_transcript_retries,
    pop_llm_candidate=llm_repo.pop_llm_candidate,
    mark_restructure_processing=llm_repo.mark_restructure_processing,
    mark_restructure_failed=llm_repo.mark_restructure_failed,
    requeue_llm_pending_without_retry=llm_repo.requeue_llm_pending_without_retry,
    repair_orphan_llm_candidates=llm_repo.repair_orphan_llm_candidates,
    ensure_llm_config_missing_alert=llm_repo.ensure_llm_config_missing_alert,
    get_setting=settings_repo.get_setting,
    acquire_transcript_worker_lease=transcripts_repo.acquire_transcript_worker_lease,
    renew_transcript_worker_lease=transcripts_repo.renew_transcript_worker_lease,
    release_transcript_worker_lease=transcripts_repo.release_transcript_worker_lease,
)


def test_compute_retry_delay_seconds_uses_exponential_backoff_with_cap() -> None:
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=0) == 120
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=1) == 240
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=4) == 1920
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=20) == 3600


def test_pop_pending_transcript_videos_prioritizes_recent_and_due(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM system_alerts WHERE alert_type = ?",
            (repository.ALERT_TYPE_LLM_CONFIG_MISSING,),
        )
        conn.execute(
            "DELETE FROM app_settings WHERE key = ?",
            (repository.LLM_CONFIG_MISSING_ALERT_SENT_KEY,),
        )
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCpolicy001",
                "Policy Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCpolicy001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, transcript_next_attempt_at)
            VALUES (?, ?, ?, ?, 'transcript_pending', ?)
            """,
            ("vid-policy-old", "UCpolicy001", "old", "2026-01-01T00:00:00+00:00", None),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, transcript_next_attempt_at)
            VALUES (?, ?, ?, ?, 'transcript_pending', ?)
            """,
            (
                "vid-policy-new",
                "UCpolicy001",
                "new",
                "2026-02-01T00:00:00+00:00",
                "2000-01-01 00:00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, transcript_next_attempt_at)
            VALUES (?, ?, ?, ?, 'transcript_pending', ?)
            """,
            (
                "vid-policy-future",
                "UCpolicy001",
                "future",
                "2026-03-01T00:00:00+00:00",
                "2999-01-01 00:00:00",
            ),
        )
        conn.commit()

    async def _load_ids() -> list[str]:
        db = await open_database(db_path)
        try:
            rows = await repository.pop_pending_transcript_videos(db, limit=10)
            return [row["video_id"] for row in rows]
        finally:
            await db.close()

    ids = asyncio.run(_load_ids())
    assert ids == ["vid-policy-new", "vid-policy-old"]


def test_schedule_transcript_retry_updates_retry_count_and_next_attempt(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCretry001",
                "Retry Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCretry001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'transcript_pending')
            """,
            ("vid-retry-001", "UCretry001", "retry", "2026-02-10T00:00:00+00:00"),
        )
        conn.commit()

    async def _run() -> tuple[int, str | None]:
        db = await open_database(db_path)
        try:
            await repository.schedule_transcript_retry(db, "vid-retry-001", delay_seconds=120)
            cursor = await db.execute(
                "SELECT transcript_retry_count, transcript_next_attempt_at FROM videos WHERE video_id = ?",
                ("vid-retry-001",),
            )
            row = await cursor.fetchone()
            return int(row["transcript_retry_count"]), row["transcript_next_attempt_at"]
        finally:
            await db.close()

    retry_count, next_attempt_at = asyncio.run(_run())
    assert retry_count == 1
    assert next_attempt_at is not None


def test_recover_stuck_jobs_leaves_transcript_processing_untouched(tmp_path) -> None:
    db_path = tmp_path / "recover-stuck-jobs.db"

    async def _run() -> tuple[int, str, str]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await db.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (
                    "UCrecover001",
                    "Recover Channel",
                    "https://www.youtube.com/feeds/videos.xml?channel_id=UCrecover001",
                ),
            )
            await db.execute(
                """
                INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
                VALUES
                  (?, ?, ?, ?, 'llm_processing'),
                  (?, ?, ?, ?, 'transcript_processing')
                """,
                (
                    "vid-recover-llm",
                    "UCrecover001",
                    "recover llm",
                    "2026-02-10T00:00:00+00:00",
                    "vid-recover-transcript",
                    "UCrecover001",
                    "recover transcript",
                    "2026-02-10T00:00:00+00:00",
                ),
            )
            await db.commit()
            recovered = await recover_stuck_jobs(db)
            cursor = await db.execute(
                """
                SELECT video_id, pipeline_status
                FROM videos
                WHERE video_id IN ('vid-recover-llm', 'vid-recover-transcript')
                ORDER BY video_id
                """
            )
            rows = await cursor.fetchall()
            return recovered, str(rows[0]["pipeline_status"]), str(rows[1]["pipeline_status"])
        finally:
            await db.close()

    recovered, llm_status, transcript_status = asyncio.run(_run())
    assert recovered == 1
    assert llm_status == "llm_pending"
    assert transcript_status == "transcript_processing"


def test_recover_stuck_transcript_jobs_requeues_processing_rows(tmp_path) -> None:
    db_path = tmp_path / "recover-stuck-transcript-jobs.db"

    async def _run() -> tuple[int, str]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await db.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (
                    "UCrecover002",
                    "Recover Channel 2",
                    "https://www.youtube.com/feeds/videos.xml?channel_id=UCrecover002",
                ),
            )
            await db.execute(
                """
                INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
                VALUES (?, ?, ?, ?, 'transcript_processing')
                """,
                (
                    "vid-recover-transcript-only",
                    "UCrecover002",
                    "recover transcript only",
                    "2026-02-10T00:00:00+00:00",
                ),
            )
            await db.commit()
            recovered = await repository.recover_stuck_transcript_jobs(db)
            cursor = await db.execute(
                "SELECT pipeline_status FROM videos WHERE video_id = ?",
                ("vid-recover-transcript-only",),
            )
            row = await cursor.fetchone()
            return recovered, str(row["pipeline_status"])
        finally:
            await db.close()

    recovered, transcript_status = asyncio.run(_run())
    assert recovered == 1
    assert transcript_status == "transcript_pending"


def test_transcript_fetcher_recovers_processing_rows_when_lease_disabled(tmp_path) -> None:
    db_path = tmp_path / "recover-processing-lease-disabled.db"

    class _FakeTranscriptService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def fetch_transcript(self, video_id: str, preferred_language: str | None = None):
            self.calls.append(video_id)
            return "recovered transcript", "ko", "manual"

    async def _run() -> tuple[str, str | None, list[str]]:
        db = await open_database(str(db_path))
        fake_service = _FakeTranscriptService()
        try:
            await init_database(db)
            await db.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (
                    "UCrecover003",
                    "Recover Channel 3",
                    "https://www.youtube.com/feeds/videos.xml?channel_id=UCrecover003",
                ),
            )
            await db.execute(
                """
                INSERT INTO videos(
                    video_id,
                    channel_id,
                    title,
                    upload_time,
                    pipeline_status,
                    processing_stage_snapshot
                )
                VALUES (?, ?, ?, ?, 'transcript_processing', 'transcript_only')
                """,
                (
                    "vid-recover-lease-disabled",
                    "UCrecover003",
                    "recover lease disabled",
                    "2026-02-10T00:00:00+00:00",
                ),
            )
            await db.commit()
            state = SimpleNamespace(
                config=SimpleNamespace(
                    db_path=str(db_path),
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
                    transcript_worker_lease_enabled=False,
                    transcript_worker_lease_ttl_seconds=5,
                ),
                db=db,
                transcript_service=fake_service,
            )
            task = asyncio.create_task(run_transcript_fetcher(state))
            try:
                for _ in range(80):
                    cursor = await db.execute(
                        """
                        SELECT v.pipeline_status, t.raw_text
                        FROM videos v
                        LEFT JOIN transcripts t ON t.video_id = v.video_id
                        WHERE v.video_id = ?
                        """,
                        ("vid-recover-lease-disabled",),
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
    assert status == "transcript_done"
    assert raw_text == "recovered transcript"
    assert calls == ["vid-recover-lease-disabled"]


def test_schedule_transcript_retry_persists_last_error(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCretry002",
                "Retry Channel 2",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCretry002",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'transcript_pending')
            """,
            ("vid-retry-002", "UCretry002", "retry2", "2026-02-11T00:00:00+00:00"),
        )
        conn.commit()

    async def _run() -> tuple[int, str | None]:
        db = await open_database(db_path)
        try:
            await repository.schedule_transcript_retry(
                db,
                "vid-retry-002",
                delay_seconds=120,
                error_message="network timeout",
            )
            cursor = await db.execute(
                "SELECT transcript_retry_count, transcript_last_error FROM videos WHERE video_id = ?",
                ("vid-retry-002",),
            )
            row = await cursor.fetchone()
            return int(row["transcript_retry_count"]), row["transcript_last_error"]
        finally:
            await db.close()

    retry_count, last_error = asyncio.run(_run())
    assert retry_count == 1
    assert last_error == "network timeout"


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

        def fetch(self, video_id, languages):
            observed_headers.update(dict(self.http_client.headers))
            return _FakeFetchedTranscript()

    monkeypatch.setattr(
        transcript_service_module, "YouTubeTranscriptApi", _FakeYouTubeTranscriptApi
    )
    service = transcript_service_module.TranscriptService(
        client=None,  # type: ignore[arg-type]
        request_timeout_seconds=7,
    )
    service.apply_transcript_request_headers({"Accept-Language": "ko-KR,ko;q=0.9"})

    raw_text, language, source_type = service._fetch_transcript_sync("vid-header-001", "ko")

    assert raw_text == "hello"
    assert language == "ko"
    assert source_type == "manual"
    assert observed_headers["Accept-Language"] == "ko-KR,ko;q=0.9"


def test_save_transcript_sets_target_language_and_clears_last_error(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCsave001",
                "Save Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCsave001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(
                video_id,
                channel_id,
                title,
                upload_time,
                pipeline_status,
                transcript_last_error,
                transcript_last_error_at
            ) VALUES (?, ?, ?, ?, 'transcript_pending', ?, ?)
            """,
            (
                "vid-save-001",
                "UCsave001",
                "save",
                "2026-02-12T00:00:00+00:00",
                "old error",
                "2026-02-12T00:00:00+00:00",
            ),
        )
        conn.commit()

    async def _run() -> tuple[str, str | None, str | None, str | None]:
        db = await open_database(db_path)
        try:
            await repository.save_transcript(
                db,
                video_id="vid-save-001",
                raw_text="hello",
                language="ko",
                source_type="manual",
                thumbnail_path=None,
            )
            cursor = await db.execute(
                """
                SELECT pipeline_status, transcript_target_language, transcript_last_error, transcript_last_error_at
                FROM videos
                WHERE video_id = ?
                """,
                ("vid-save-001",),
            )
            row = await cursor.fetchone()
            return (
                str(row["pipeline_status"]),
                row["transcript_target_language"],
                row["transcript_last_error"],
                row["transcript_last_error_at"],
            )
        finally:
            await db.close()

    status, target_language, last_error, last_error_at = asyncio.run(_run())
    assert status == "llm_pending"
    assert target_language == "ko"
    assert last_error is None
    assert last_error_at is None


def test_pop_pending_transcript_videos_can_avoid_last_channel(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1), (?, ?, ?, 1)
            """,
            (
                "UCfair001",
                "Fair Channel A",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCfair001",
                "UCfair002",
                "Fair Channel B",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCfair002",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES
              (?, ?, ?, ?, 'transcript_pending'),
              (?, ?, ?, ?, 'transcript_pending')
            """,
            (
                "vid-fair-a",
                "UCfair001",
                "newest",
                "2026-02-28T00:00:00+00:00",
                "vid-fair-b",
                "UCfair002",
                "older",
                "2026-02-27T00:00:00+00:00",
            ),
        )
        conn.commit()

    async def _run() -> tuple[str, str]:
        db = await open_database(db_path)
        try:
            normal = await repository.pop_pending_transcript_videos(db, limit=1, lookahead=10)
            avoided = await repository.pop_pending_transcript_videos(
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


def test_defer_channel_transcript_retries_defers_same_channel_except_excluded(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCdefer001",
                "Defer Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCdefer001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES
              (?, ?, ?, ?, 'transcript_pending'),
              (?, ?, ?, ?, 'transcript_pending')
            """,
            (
                "vid-defer-keep",
                "UCdefer001",
                "keep",
                "2026-02-25T00:00:00+00:00",
                "vid-defer-move",
                "UCdefer001",
                "move",
                "2026-02-24T00:00:00+00:00",
            ),
        )
        conn.commit()

    async def _run() -> tuple[str | None, str | None]:
        db = await open_database(db_path)
        try:
            await repository.defer_channel_transcript_retries(
                db,
                channel_id="UCdefer001",
                delay_seconds=600,
                exclude_video_id="vid-defer-keep",
            )
            cursor = await db.execute(
                """
                SELECT video_id, transcript_next_attempt_at
                FROM videos
                WHERE video_id IN ('vid-defer-keep', 'vid-defer-move')
                ORDER BY video_id ASC
                """
            )
            rows = await cursor.fetchall()
            return rows[0]["transcript_next_attempt_at"], rows[1]["transcript_next_attempt_at"]
        finally:
            await db.close()

    keep_next_attempt_at, move_next_attempt_at = asyncio.run(_run())
    assert keep_next_attempt_at is None
    assert move_next_attempt_at is not None


def test_mark_restructure_failed_skips_when_video_is_not_llm_processing(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCllm001",
                "LLM Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCllm001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'done', 0)
            """,
            ("vid-llm-001", "UCllm001", "done-video", "2026-02-21T00:00:00+00:00"),
        )
        conn.commit()

    async def _run() -> tuple[str, int, str, int]:
        db = await open_database(db_path)
        try:
            next_status, affected = await repository.mark_restructure_failed(
                db,
                video_id="vid-llm-001",
                retry_count=0,
                max_retry_count=3,
            )
            cursor = await db.execute(
                "SELECT pipeline_status, retry_count FROM videos WHERE video_id = ?",
                ("vid-llm-001",),
            )
            row = await cursor.fetchone()
            return next_status, affected, str(row["pipeline_status"]), int(row["retry_count"])
        finally:
            await db.close()

    next_status, affected, pipeline_status, retry_count = asyncio.run(_run())
    assert next_status == "llm_failed"
    assert affected == 0
    assert pipeline_status == "done"
    assert retry_count == 0


def test_llm_retry_count_floor_allows_at_least_one_attempt(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCllmfloor001",
                "LLM Floor Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCllmfloor001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_pending', 0)
            """,
            ("vid-llm-floor-001", "UCllmfloor001", "floor-video", "2026-02-21T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-llm-floor-001", "hello", "ko", "manual"),
        )
        conn.commit()

    async def _run() -> tuple[str | None, int, str, int, str, int]:
        db = await open_database(db_path)
        try:
            candidate = await repository.pop_llm_candidate(db, max_retry_count=0)
            processing_affected = await repository.mark_restructure_processing(
                db,
                candidate["video_id"] if candidate else "",
            )
            next_status, failed_affected = await repository.mark_restructure_failed(
                db,
                video_id="vid-llm-floor-001",
                retry_count=0,
                max_retry_count=0,
            )
            cursor = await db.execute(
                "SELECT pipeline_status, retry_count FROM videos WHERE video_id = ?",
                ("vid-llm-floor-001",),
            )
            row = await cursor.fetchone()
            return (
                str(candidate["video_id"]) if candidate else None,
                processing_affected,
                next_status,
                failed_affected,
                str(row["pipeline_status"]),
                int(row["retry_count"]),
            )
        finally:
            await db.close()

    candidate_id, processing_affected, next_status, affected, pipeline_status, retry_count = (
        asyncio.run(_run())
    )
    assert candidate_id == "vid-llm-floor-001"
    assert processing_affected == 1
    assert next_status == "manual_review"
    assert affected == 1
    assert pipeline_status == "manual_review"
    assert retry_count == 1


def test_requeue_llm_pending_without_retry_keeps_retry_count(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCrequeue001",
                "Requeue Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCrequeue001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_processing', 2)
            """,
            ("vid-requeue-001", "UCrequeue001", "requeue-video", "2026-02-21T00:00:00+00:00"),
        )
        conn.commit()

    async def _run() -> tuple[int, str, int]:
        db = await open_database(db_path)
        try:
            affected = await repository.requeue_llm_pending_without_retry(
                db,
                video_id="vid-requeue-001",
            )
            cursor = await db.execute(
                "SELECT pipeline_status, retry_count FROM videos WHERE video_id = ?",
                ("vid-requeue-001",),
            )
            row = await cursor.fetchone()
            return affected, str(row["pipeline_status"]), int(row["retry_count"])
        finally:
            await db.close()

    affected, pipeline_status, retry_count = asyncio.run(_run())
    assert affected == 1
    assert pipeline_status == "llm_pending"
    assert retry_count == 2


def test_repair_orphan_llm_candidates_moves_only_orphans_to_manual_review(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCorphan001",
                "Orphan Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCorphan001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES
              (?, ?, ?, ?, 'llm_pending'),
              (?, ?, ?, ?, 'llm_failed'),
              (?, ?, ?, ?, 'llm_pending')
            """,
            (
                "vid-orphan-pending",
                "UCorphan001",
                "orphan pending",
                "2026-02-22T00:00:00+00:00",
                "vid-orphan-failed",
                "UCorphan001",
                "orphan failed",
                "2026-02-22T00:00:00+00:00",
                "vid-orphan-safe",
                "UCorphan001",
                "has transcript",
                "2026-02-22T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-orphan-safe", "hello", "ko", "manual"),
        )
        conn.commit()

    async def _run() -> tuple[int, dict[str, str]]:
        db = await open_database(db_path)
        try:
            repaired = await repository.repair_orphan_llm_candidates(db)
            cursor = await db.execute(
                """
                SELECT video_id, pipeline_status
                FROM videos
                WHERE video_id IN ('vid-orphan-pending', 'vid-orphan-failed', 'vid-orphan-safe')
                ORDER BY video_id
                """
            )
            rows = await cursor.fetchall()
            statuses = {str(row["video_id"]): str(row["pipeline_status"]) for row in rows}
            return repaired, statuses
        finally:
            await db.close()

    repaired, statuses = asyncio.run(_run())
    assert repaired == 2
    assert statuses["vid-orphan-pending"] == "manual_review"
    assert statuses["vid-orphan-failed"] == "manual_review"
    assert statuses["vid-orphan-safe"] == "llm_pending"


def test_ensure_llm_config_missing_alert_is_deduplicated_and_reset(client) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCalert001",
                "Alert Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCalert001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'llm_pending')
            """,
            ("vid-alert-001", "UCalert001", "alert-video", "2026-02-23T00:00:00+00:00"),
        )
        conn.commit()

    async def _run() -> tuple[bool, bool, int, str | None]:
        db = await open_database(db_path)
        try:
            created_first = await repository.ensure_llm_config_missing_alert(db)
            created_second = await repository.ensure_llm_config_missing_alert(db)
            await db.execute(
                "UPDATE videos SET pipeline_status = 'done' WHERE video_id = 'vid-alert-001'"
            )
            await db.commit()
            created_after_done = await repository.ensure_llm_config_missing_alert(db)
            assert created_after_done is False

            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM system_alerts WHERE alert_type = ?",
                (repository.ALERT_TYPE_LLM_CONFIG_MISSING,),
            )
            count_row = await cursor.fetchone()
            sent_key = await repository.get_setting(
                db,
                key=repository.LLM_CONFIG_MISSING_ALERT_SENT_KEY,
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


def test_transcript_worker_lease_allows_single_owner(tmp_path) -> None:
    db_path = tmp_path / "lease.db"

    async def _run() -> tuple[bool, bool, bool, bool, bool, bool]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            acquired_a = await repository.acquire_transcript_worker_lease(
                db,
                owner_id="owner-a",
                ttl_seconds=60,
            )
            acquired_b = await repository.acquire_transcript_worker_lease(
                db,
                owner_id="owner-b",
                ttl_seconds=60,
            )
            renewed_a = await repository.renew_transcript_worker_lease(
                db,
                owner_id="owner-a",
                ttl_seconds=60,
            )
            released_b = await repository.release_transcript_worker_lease(db, owner_id="owner-b")
            released_a = await repository.release_transcript_worker_lease(db, owner_id="owner-a")
            acquired_b_after_release = await repository.acquire_transcript_worker_lease(
                db,
                owner_id="owner-b",
                ttl_seconds=60,
            )
            return (
                acquired_a,
                acquired_b,
                renewed_a,
                released_b,
                released_a,
                acquired_b_after_release,
            )
        finally:
            await db.close()

    acquired_a, acquired_b, renewed_a, released_b, released_a, acquired_b_after_release = (
        asyncio.run(_run())
    )
    assert acquired_a is True
    assert acquired_b is False
    assert renewed_a is True
    assert released_b is False
    assert released_a is True
    assert acquired_b_after_release is True


def test_transcript_worker_lease_succeeds_with_dedicated_connection_while_shared_connection_has_transaction(
    tmp_path,
) -> None:
    db_path = tmp_path / "lease-dedicated.db"

    async def _run() -> bool:
        shared_db = await open_database(str(db_path))
        worker_db = await open_database(str(db_path))
        try:
            await init_database(shared_db)
            await shared_db.execute("BEGIN")
            await shared_db.execute("SELECT 1")
            acquired = await repository.acquire_transcript_worker_lease(
                worker_db,
                owner_id="owner-dedicated",
                ttl_seconds=60,
            )
            await shared_db.rollback()
            return acquired
        finally:
            await worker_db.close()
            await shared_db.close()

    acquired = asyncio.run(_run())
    assert acquired is True


def test_lease_heartbeat_loop_renews_before_stop(tmp_path, monkeypatch) -> None:
    from app.workers import transcript_worker

    db_path = tmp_path / "lease-heartbeat.db"

    async def _run() -> int:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await repository.acquire_transcript_worker_lease(
                db,
                owner_id="owner-heartbeat",
                ttl_seconds=60,
            )
            stop_event = asyncio.Event()
            lost_event = asyncio.Event()
            renew_calls = {"count": 0}
            original = repository.renew_transcript_worker_lease

            async def _wrapped_renew(*args, **kwargs):
                renew_calls["count"] += 1
                stop_event.set()
                return await original(*args, **kwargs)

            monkeypatch.setattr(
                transcript_worker.transcripts_repo, "renew_transcript_worker_lease", _wrapped_renew
            )

            await transcript_worker._lease_heartbeat_loop(
                db=db,
                owner_id="owner-heartbeat",
                ttl_seconds=60,
                renew_interval_seconds=0.01,
                stop_event=stop_event,
                lost_event=lost_event,
            )
            assert lost_event.is_set() is False
            return renew_calls["count"]
        finally:
            await db.close()

    renew_count = asyncio.run(_run())
    assert renew_count >= 1
