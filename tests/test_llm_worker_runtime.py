from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import repository
from app.config import AppConfig
from app.database import init_database, open_database
from app.services.llm import LlmClientError, LlmRuntimePlan
from app.workers.llm_worker import run_llm_queue_worker


class AuthRequiredClient:
    def resolve_runtime_plan(self, settings):
        return LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        )

    async def restructure(self, source_title: str, transcript_text: str, settings):
        raise LlmClientError(
            "llm_provider_auth_required",
            "Not logged in",
            provider="codex",
            retryable=False,
        )


class SchemaInvalidClient:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_runtime_plan(self, settings):
        return LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        )

    async def restructure(self, source_title: str, transcript_text: str, settings):
        self.calls += 1
        raise LlmClientError(
            "llm_provider_schema_invalid_codex",
            "invalid_json_schema",
            provider="codex",
            retryable=False,
        )


class DelayedSuccessClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()

    def resolve_runtime_plan(self, settings):
        return LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        )

    async def restructure(self, source_title: str, transcript_text: str, settings):
        self.started.set()
        await asyncio.wait_for(self.proceed.wait(), timeout=2)
        return {
            "title": "Article title",
            "lead": "Article lead",
            "body": "Article body",
            "fact_box": "{}",
            "timestamps": "[]",
        }


class DelayedRetryableFailureClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()

    def resolve_runtime_plan(self, settings):
        return LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        )

    async def restructure(self, source_title: str, transcript_text: str, settings):
        self.started.set()
        await asyncio.wait_for(self.proceed.wait(), timeout=2)
        raise LlmClientError(
            "llm_schema_invalid",
            "LLM response does not match required article schema",
            provider="codex",
            retryable=True,
        )


async def _wait_for_video_status(
    db,
    *,
    video_id: str,
    expected: str,
    timeout_seconds: float = 3.0,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        row = await repository.get_video(db, video_id)
        if row and str(row.get("pipeline_status")) == expected:
            return row
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"video {video_id} did not reach status={expected}")
        await asyncio.sleep(0.05)


def test_llm_worker_requeues_pending_when_auth_is_required(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime.db"

    async def _run() -> tuple[str, int, int, str | None, dict[str, str]]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCworker001", "Worker Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker001"),
        )
        await db.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_pending', 2)
            """,
            ("vid-worker-001", "UCworker001", "worker-video", "2026-02-24T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-worker-001", "hello transcript", "ko", "manual"),
        )
        await db.commit()

        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=AuthRequiredClient(),
            notification_queue=asyncio.Queue(),
        )

        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            alert_count = 0
            for _ in range(60):
                cursor = await db.execute(
                    "SELECT COUNT(1) AS cnt FROM system_alerts WHERE alert_type = ?",
                    (repository.ALERT_TYPE_LLM_CONFIG_MISSING,),
                )
                row = await cursor.fetchone()
                alert_count = int(row["cnt"] or 0)
                if alert_count > 0:
                    break
                await asyncio.sleep(0.05)

            cursor = await db.execute(
                "SELECT pipeline_status, retry_count FROM videos WHERE video_id = ?",
                ("vid-worker-001",),
            )
            video = await cursor.fetchone()
            sent_key = await repository.get_setting(
                db,
                key=repository.LLM_CONFIG_MISSING_ALERT_SENT_KEY,
                default=None,
            )
            runtime_issue = await repository.get_llm_runtime_issue(db)
            return str(video["pipeline_status"]), int(video["retry_count"]), alert_count, sent_key, runtime_issue
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    status, retry_count, alert_count, sent_key, runtime_issue = asyncio.run(_run())
    assert status == "llm_pending"
    assert retry_count == 2
    assert alert_count >= 1
    assert sent_key == "1"
    assert runtime_issue["code"] == "llm_provider_auth_required"


def test_llm_worker_hard_stops_when_schema_is_invalid(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-schema.db"

    async def _run() -> tuple[str, int, int, str | None, dict[str, str], int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCworker002", "Worker Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker002"),
        )
        await db.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_pending', 1)
            """,
            ("vid-worker-002", "UCworker002", "worker-video-2", "2026-02-24T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-worker-002", "hello transcript", "ko", "manual"),
        )
        await db.commit()

        client = SchemaInvalidClient()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
        )

        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            alert_count = 0
            for _ in range(60):
                cursor = await db.execute(
                    "SELECT COUNT(1) AS cnt FROM system_alerts WHERE alert_type = ?",
                    (repository.ALERT_TYPE_LLM_SCHEMA_INVALID,),
                )
                row = await cursor.fetchone()
                alert_count = int(row["cnt"] or 0)
                if alert_count > 0:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.25)

            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM system_alerts WHERE alert_type = ?",
                (repository.ALERT_TYPE_LLM_SCHEMA_INVALID,),
            )
            row = await cursor.fetchone()
            alert_count = int(row["cnt"] or 0)

            cursor = await db.execute(
                "SELECT pipeline_status, retry_count FROM videos WHERE video_id = ?",
                ("vid-worker-002",),
            )
            video = await cursor.fetchone()
            sent_key = await repository.get_setting(
                db,
                key=repository.LLM_SCHEMA_INVALID_ALERT_SENT_KEY,
                default=None,
            )
            runtime_issue = await repository.get_llm_runtime_issue(db)
            return (
                str(video["pipeline_status"]),
                int(video["retry_count"]),
                alert_count,
                sent_key,
                runtime_issue,
                client.calls,
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    status, retry_count, alert_count, sent_key, runtime_issue, call_count = asyncio.run(_run())
    assert status == "llm_pending"
    assert retry_count == 1
    assert alert_count == 1
    assert sent_key == "1"
    assert runtime_issue["code"] == "llm_provider_schema_invalid_codex"
    assert call_count == 1


def test_llm_worker_state_transition_pending_to_processing_to_done(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-transition-success.db"

    async def _run() -> tuple[str, int, int, str]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCworker003", "Worker Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker003"),
        )
        await db.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_pending', 0)
            """,
            ("vid-worker-003", "UCworker003", "worker-video-3", "2026-02-24T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-worker-003", "hello transcript", "ko", "manual"),
        )
        await repository.set_llm_runtime_issue(
            db,
            code="llm_unknown_issue",
            message="stale issue",
        )
        await db.commit()

        client = DelayedSuccessClient()
        queue = asyncio.Queue()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=queue,
            llm_wake_event=asyncio.Event(),
        )

        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            await asyncio.wait_for(client.started.wait(), timeout=2)
            processing_row = await _wait_for_video_status(
                db,
                video_id="vid-worker-003",
                expected="llm_processing",
                timeout_seconds=2,
            )
            assert int(processing_row["retry_count"]) == 0

            client.proceed.set()
            done_row = await _wait_for_video_status(
                db,
                video_id="vid-worker-003",
                expected="done",
                timeout_seconds=2,
            )
            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM articles WHERE video_id = ?",
                ("vid-worker-003",),
            )
            article_row = await cursor.fetchone()
            runtime_issue = await repository.get_llm_runtime_issue(db)
            notice = await asyncio.wait_for(queue.get(), timeout=1)
            assert notice.get("video_id") == "vid-worker-003"
            return (
                str(done_row["pipeline_status"]),
                int(done_row["retry_count"]),
                int(article_row["cnt"] or 0),
                runtime_issue["code"] if runtime_issue else "",
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    status, retry_count, article_count, runtime_issue_code = asyncio.run(_run())
    assert status == "done"
    assert retry_count == 0
    assert article_count == 1
    assert runtime_issue_code == ""


def test_llm_worker_state_transition_processing_to_manual_review_on_retry_exhausted(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-transition-fail.db"

    async def _run() -> tuple[str, int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCworker004", "Worker Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker004"),
        )
        await db.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_pending', 2)
            """,
            ("vid-worker-004", "UCworker004", "worker-video-4", "2026-02-24T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-worker-004", "hello transcript", "ko", "manual"),
        )
        await db.commit()

        client = DelayedRetryableFailureClient()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )

        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            await asyncio.wait_for(client.started.wait(), timeout=2)
            await _wait_for_video_status(
                db,
                video_id="vid-worker-004",
                expected="llm_processing",
                timeout_seconds=2,
            )
            client.proceed.set()
            manual_review_row = await _wait_for_video_status(
                db,
                video_id="vid-worker-004",
                expected="manual_review",
                timeout_seconds=2,
            )
            return (
                str(manual_review_row["pipeline_status"]),
                int(manual_review_row["retry_count"]),
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    status, retry_count = asyncio.run(_run())
    assert status == "manual_review"
    assert retry_count == 3
