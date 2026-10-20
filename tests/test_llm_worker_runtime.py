from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.config import AppConfig
from app.database import init_database, open_database
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.services.llm import LlmClientError, LlmRuntimePlan
from app.workers.llm_worker import run_llm_queue_worker

repository = SimpleNamespace(
    get_video=videos_repo.get_video,
    ALERT_TYPE_LLM_CONFIG_MISSING=llm_repo.ALERT_TYPE_LLM_CONFIG_MISSING,
    LLM_CONFIG_MISSING_ALERT_SENT_KEY=llm_repo.LLM_CONFIG_MISSING_ALERT_SENT_KEY,
    ALERT_TYPE_LLM_SCHEMA_INVALID=llm_repo.ALERT_TYPE_LLM_SCHEMA_INVALID,
    LLM_SCHEMA_INVALID_ALERT_SENT_KEY=llm_repo.LLM_SCHEMA_INVALID_ALERT_SENT_KEY,
    get_setting=settings_repo.get_setting,
    get_llm_runtime_issue=llm_repo.get_llm_runtime_issue,
    set_llm_runtime_issue=llm_repo.set_llm_runtime_issue,
)


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
            "_llm_provider": "codex",
            "_llm_model": "gpt-5.3-codex",
            "_llm_reasoning_effort": "medium",
            "_llm_generated_at": "2026-03-02T10:00:00+00:00",
        }


class ConcurrentSuccessClient:
    def __init__(self) -> None:
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.proceed = asyncio.Event()

    def resolve_runtime_plan(self, settings):
        return LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        )

    async def restructure(self, source_title: str, transcript_text: str, settings):
        await self.started.put(source_title)
        await asyncio.wait_for(self.proceed.wait(), timeout=2)
        return {
            "title": f"Article {source_title}",
            "lead": "Article lead",
            "body": "Article body",
            "fact_box": "{}",
            "timestamps": "[]",
            "_llm_provider": "codex",
            "_llm_model": "gpt-5.3-codex",
            "_llm_reasoning_effort": "medium",
            "_llm_generated_at": "2026-03-02T10:00:00+00:00",
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


class DelayedNonRetryableFailureClient:
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
            "llm_provider_refused",
            "Prompt injection refusal",
            provider="codex",
            retryable=False,
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
            (
                "UCworker001",
                "Worker Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker001",
            ),
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
            return (
                str(video["pipeline_status"]),
                int(video["retry_count"]),
                alert_count,
                sent_key,
                runtime_issue,
            )
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
            (
                "UCworker002",
                "Worker Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker002",
            ),
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

    async def _run() -> tuple[str, int, int, str, str, str, str, str]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCworker003",
                "Worker Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker003",
            ),
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
            code="llm_provider_unavailable_codex",
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
            article_meta_cursor = await db.execute(
                """
                SELECT llm_provider, llm_model, llm_reasoning_effort, llm_generated_at
                FROM articles
                WHERE video_id = ?
                """,
                ("vid-worker-003",),
            )
            article_meta = await article_meta_cursor.fetchone()
            runtime_issue = await repository.get_llm_runtime_issue(db)
            notice = await asyncio.wait_for(queue.get(), timeout=1)
            assert notice.get("video_id") == "vid-worker-003"
            return (
                str(done_row["pipeline_status"]),
                int(done_row["retry_count"]),
                int(article_row["cnt"] or 0),
                runtime_issue["code"] if runtime_issue else "",
                str(article_meta["llm_provider"] if article_meta is not None else ""),
                str(article_meta["llm_model"] if article_meta is not None else ""),
                str(article_meta["llm_reasoning_effort"] if article_meta is not None else ""),
                str(article_meta["llm_generated_at"] if article_meta is not None else ""),
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    (
        status,
        retry_count,
        article_count,
        runtime_issue_code,
        llm_provider,
        llm_model,
        llm_effort,
        llm_generated_at,
    ) = asyncio.run(_run())
    assert status == "done"
    assert retry_count == 0
    assert article_count == 1
    assert runtime_issue_code == ""
    assert llm_provider == "codex"
    assert llm_model == "gpt-5.3-codex"
    assert llm_effort == "medium"
    assert llm_generated_at == "2026-03-02T10:00:00+00:00"


def test_llm_worker_processes_multiple_candidates_concurrently(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-concurrent.db"

    async def _run() -> tuple[set[str], set[str], int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCworker008",
                "Worker Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker008",
            ),
        )
        for video_id, title in (
            ("vid-worker-008a", "worker-video-8a"),
            ("vid-worker-008b", "worker-video-8b"),
        ):
            await db.execute(
                """
                INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
                VALUES (?, ?, ?, ?, 'llm_pending', 0)
                """,
                (video_id, "UCworker008", title, "2026-02-24T00:00:00+00:00"),
            )
            await db.execute(
                """
                INSERT INTO transcripts(video_id, raw_text, language, source_type)
                VALUES (?, ?, ?, ?)
                """,
                (video_id, f"hello transcript {video_id}", "ko", "manual"),
            )
        await settings_repo.set_llm_settings(db, max_concurrent=2)
        await db.commit()

        client = ConcurrentSuccessClient()
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
            started = {
                await asyncio.wait_for(client.started.get(), timeout=2),
                await asyncio.wait_for(client.started.get(), timeout=2),
            }
            processing_ids = set()
            for video_id in ("vid-worker-008a", "vid-worker-008b"):
                row = await _wait_for_video_status(
                    db,
                    video_id=video_id,
                    expected="llm_processing",
                    timeout_seconds=2,
                )
                processing_ids.add(str(row["video_id"]))

            client.proceed.set()
            for video_id in ("vid-worker-008a", "vid-worker-008b"):
                await _wait_for_video_status(
                    db,
                    video_id=video_id,
                    expected="done",
                    timeout_seconds=2,
                )
            cursor = await db.execute("SELECT COUNT(1) AS cnt FROM articles")
            article_row = await cursor.fetchone()
            return started, processing_ids, int(article_row["cnt"] or 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    started, processing_ids, article_count = asyncio.run(_run())
    assert started == {"worker-video-8a", "worker-video-8b"}
    assert processing_ids == {"vid-worker-008a", "vid-worker-008b"}
    assert article_count == 2


def test_llm_worker_processes_retryable_llm_failed_candidate(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-retryable-failed.db"

    async def _run() -> tuple[str, int, int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCworker005",
                "Worker Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker005",
            ),
        )
        await db.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_failed', 1)
            """,
            ("vid-worker-005", "UCworker005", "worker-video-5", "2026-02-24T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-worker-005", "hello transcript", "ko", "manual"),
        )
        await db.commit()

        client = DelayedSuccessClient()
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
                video_id="vid-worker-005",
                expected="llm_processing",
                timeout_seconds=2,
            )
            client.proceed.set()
            done_row = await _wait_for_video_status(
                db,
                video_id="vid-worker-005",
                expected="done",
                timeout_seconds=2,
            )
            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM articles WHERE video_id = ?",
                ("vid-worker-005",),
            )
            article_row = await cursor.fetchone()
            return (
                str(done_row["pipeline_status"]),
                int(done_row["retry_count"]),
                int(article_row["cnt"] or 0),
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    status, retry_count, article_count = asyncio.run(_run())
    assert status == "done"
    assert retry_count == 1
    assert article_count == 1


def test_llm_worker_stale_success_does_not_overwrite_changed_status(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-stale-success.db"

    async def _run() -> tuple[str, int, int, bool]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCworker006",
                "Worker Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker006",
            ),
        )
        await db.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_pending', 0)
            """,
            ("vid-worker-006", "UCworker006", "worker-video-6", "2026-02-24T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-worker-006", "hello transcript", "ko", "manual"),
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
            await _wait_for_video_status(
                db,
                video_id="vid-worker-006",
                expected="llm_processing",
                timeout_seconds=2,
            )
            await db.execute(
                "UPDATE videos SET pipeline_status = 'manual_review' WHERE video_id = ?",
                ("vid-worker-006",),
            )
            await db.commit()

            client.proceed.set()
            await asyncio.sleep(0.2)
            cursor = await db.execute(
                "SELECT pipeline_status, retry_count FROM videos WHERE video_id = ?",
                ("vid-worker-006",),
            )
            video = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT COUNT(1) AS cnt FROM articles WHERE video_id = ?",
                ("vid-worker-006",),
            )
            article_row = await cursor.fetchone()
            return (
                str(video["pipeline_status"]),
                int(video["retry_count"]),
                int(article_row["cnt"] or 0),
                queue.empty(),
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    status, retry_count, article_count, queue_empty = asyncio.run(_run())
    assert status == "manual_review"
    assert retry_count == 0
    assert article_count == 0
    assert queue_empty is True


def test_llm_worker_non_retryable_refusal_goes_to_manual_review(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-non-retryable.db"

    async def _run() -> tuple[str, int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCworker007",
                "Worker Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker007",
            ),
        )
        await db.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_pending', 0)
            """,
            ("vid-worker-007", "UCworker007", "worker-video-7", "2026-02-24T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-worker-007", "hello transcript", "ko", "manual"),
        )
        await db.commit()

        client = DelayedNonRetryableFailureClient()
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
                video_id="vid-worker-007",
                expected="llm_processing",
                timeout_seconds=2,
            )
            client.proceed.set()
            manual_review_row = await _wait_for_video_status(
                db,
                video_id="vid-worker-007",
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


def test_llm_worker_state_transition_processing_to_manual_review_on_retry_exhausted(
    tmp_path,
) -> None:
    db_path = tmp_path / "worker-runtime-transition-fail.db"

    async def _run() -> tuple[str, int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCworker004",
                "Worker Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCworker004",
            ),
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


def test_save_article_keeps_cursor_provider(tmp_path) -> None:
    db_path = tmp_path / "save-article-cursor.db"

    async def _run() -> tuple[int, str, str, str]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await db.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (
                    "UCcursor001",
                    "Cursor Channel",
                    "https://www.youtube.com/feeds/videos.xml?channel_id=UCcursor001",
                ),
            )
            await db.execute(
                """
                INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
                VALUES (?, ?, ?, ?, 'llm_processing')
                """,
                ("vid-cursor-001", "UCcursor001", "cursor-video", "2026-09-28T00:00:00+00:00"),
            )
            await db.commit()

            saved = await llm_repo.save_article(
                db,
                "vid-cursor-001",
                "Article title",
                "Article lead",
                "Article body",
                "- fact",
                "- 00:00 intro",
                llm_provider="cursor",
                llm_model="cursor-grok-4.6-high",
                llm_reasoning_effort="",
            )
            cursor = await db.execute(
                "SELECT llm_provider, llm_model, llm_reasoning_effort FROM articles WHERE video_id = ?",
                ("vid-cursor-001",),
            )
            row = await cursor.fetchone()
            assert row is not None
            return (
                saved,
                str(row["llm_provider"]),
                str(row["llm_model"]),
                str(row["llm_reasoning_effort"]),
            )
        finally:
            await db.close()

    saved, provider, model, effort = asyncio.run(_run())
    assert saved > 0
    assert provider == "cursor"
    assert model == "cursor-grok-4.6-high"
    assert effort == ""


class RecordingSuccessClient:
    def __init__(self) -> None:
        self.transcripts: list[str] = []

    def resolve_runtime_plan(self, settings):
        return LlmRuntimePlan(providers_to_try=["codex"], blocking_reason=None, warnings=[])

    async def restructure(self, source_title: str, transcript_text: str, settings):
        self.transcripts.append(transcript_text)
        return {
            "title": "Article title",
            "lead": "Article lead",
            "body": "Article body",
            "fact_box": "{}",
            "timestamps": "[]",
            "_llm_provider": "codex",
            "_llm_model": "gpt-5.3-codex",
            "_llm_reasoning_effort": "",
            "_llm_generated_at": "2026-03-02T10:00:00+00:00",
        }


def test_llm_worker_sends_timed_transcript_when_available(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-timed-text.db"

    async def _run() -> list[str]:
        db = await open_database(str(db_path))
        await init_database(db)
        await db.execute(
            "INSERT INTO channels(channel_id, channel_name, rss_url, is_active) "
            "VALUES ('UCtimed', 'Timed', 'https://example.invalid/rss', 1)"
        )
        for video_id, timed_text in (("vid-timed", "[00:00] hello"), ("vid-plain", None)):
            await db.execute(
                """
                INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
                VALUES (?, 'UCtimed', ?, '2026-02-24T00:00:00+00:00', 'llm_pending', 0)
                """,
                (video_id, video_id),
            )
            await db.execute(
                """
                INSERT INTO transcripts(video_id, raw_text, timed_text, language, source_type)
                VALUES (?, 'hello', ?, 'en', 'auto')
                """,
                (video_id, timed_text),
            )
        await db.commit()

        client = RecordingSuccessClient()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )
        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            for video_id in ("vid-timed", "vid-plain"):
                await _wait_for_video_status(db, video_id=video_id, expected="done")
            return sorted(client.transcripts)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    assert asyncio.run(_run()) == ["[00:00] hello", "hello"]


class TimedSuccessClient:
    def __init__(self) -> None:
        self.started_at: list[float] = []

    def resolve_runtime_plan(self, settings):
        return LlmRuntimePlan(
            providers_to_try=["claude"],
            blocking_reason=None,
            warnings=[],
        )

    async def restructure(self, source_title: str, transcript_text: str, settings):
        self.started_at.append(asyncio.get_running_loop().time())
        return {
            "title": f"Article {source_title}",
            "lead": "Article lead",
            "body": "Article body",
            "fact_box": "{}",
            "timestamps": "[]",
            "_llm_provider": "claude",
            "_llm_model": "claude-sonnet-5-5",
            "_llm_reasoning_effort": "",
            "_llm_generated_at": "2026-03-02T10:00:00+00:00",
        }


class RateLimitedThenSuccessClient(TimedSuccessClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def restructure(self, source_title: str, transcript_text: str, settings):
        self.calls += 1
        if self.calls == 1:
            raise LlmClientError(
                "llm_provider_rate_limited",
                "You've hit your limit",
                provider="claude",
                retryable=True,
            )
        return await super().restructure(source_title, transcript_text, settings)


async def _seed_llm_pending_videos(db, video_ids: tuple[str, ...]) -> None:
    await db.execute(
        """
        INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (
            "UCworkerclaude",
            "Worker Channel",
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCworkerclaude",
        ),
    )
    for video_id in video_ids:
        await db.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, 'llm_pending', 0)
            """,
            (video_id, "UCworkerclaude", video_id, "2026-02-24T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            (video_id, f"hello transcript {video_id}", "ko", "manual"),
        )


def test_llm_worker_spaces_claude_call_starts_even_with_free_slots(tmp_path, monkeypatch) -> None:
    import app.workers.llm_worker as llm_worker

    interval = 1.5
    monkeypatch.setattr(
        llm_worker,
        "llm_min_call_interval_seconds",
        lambda provider: interval if provider == "claude" else 0.0,
    )
    db_path = tmp_path / "worker-runtime-claude-interval.db"

    async def _run() -> list[float]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_llm_pending_videos(db, ("vid-claude-a", "vid-claude-b"))
        await settings_repo.set_llm_settings(db, provider_primary="claude", max_concurrent=4)
        await db.commit()

        client = TimedSuccessClient()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )
        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            for video_id in ("vid-claude-a", "vid-claude-b"):
                await _wait_for_video_status(
                    db, video_id=video_id, expected="done", timeout_seconds=5
                )
            return client.started_at
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    started_at = asyncio.run(_run())
    assert len(started_at) == 2
    assert started_at[1] - started_at[0] >= interval - 0.05


def test_llm_worker_does_not_space_other_provider_calls(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime-codex-no-interval.db"

    async def _run() -> list[float]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_llm_pending_videos(db, ("vid-codex-a", "vid-codex-b"))
        await settings_repo.set_llm_settings(db, provider_primary="codex", max_concurrent=2)
        await db.commit()

        client = TimedSuccessClient()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )
        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            for video_id in ("vid-codex-a", "vid-codex-b"):
                await _wait_for_video_status(
                    db, video_id=video_id, expected="done", timeout_seconds=5
                )
            return client.started_at
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    started_at = asyncio.run(_run())
    assert len(started_at) == 2
    assert started_at[1] - started_at[0] < 1.0


def test_llm_worker_requeues_rate_limited_job_without_spending_retry(tmp_path, monkeypatch) -> None:
    import app.workers.llm_worker as llm_worker

    monkeypatch.setattr(llm_worker, "LLM_RATE_LIMITED_SLEEP_SECONDS", 0.2)
    monkeypatch.setattr(llm_worker, "llm_min_call_interval_seconds", lambda _provider: 0.0)
    db_path = tmp_path / "worker-runtime-claude-rate-limited.db"

    async def _run() -> tuple[int, dict]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_llm_pending_videos(db, ("vid-claude-limit",))
        await settings_repo.set_llm_settings(db, provider_primary="claude")
        await db.commit()

        client = RateLimitedThenSuccessClient()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )
        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            row = await _wait_for_video_status(
                db, video_id="vid-claude-limit", expected="done", timeout_seconds=5
            )
            return client.calls, dict(row)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    calls, row = asyncio.run(_run())
    assert calls == 2
    assert int(row["retry_count"]) == 0


def test_llm_worker_rate_limit_pauses_every_slot(tmp_path, monkeypatch) -> None:
    import app.workers.llm_worker as llm_worker

    backoff = 1.0
    monkeypatch.setattr(llm_worker, "LLM_RATE_LIMITED_SLEEP_SECONDS", backoff)
    monkeypatch.setattr(llm_worker, "llm_min_call_interval_seconds", lambda _provider: 0.0)
    db_path = tmp_path / "worker-runtime-claude-rate-limit-global.db"

    async def _run() -> list[float]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_llm_pending_videos(db, ("vid-limit-a", "vid-limit-b"))
        await settings_repo.set_llm_settings(db, provider_primary="claude", max_concurrent=4)
        await db.commit()

        client = RateLimitedThenSuccessClient()
        calls_at: list[float] = []
        original = client.restructure

        async def recording_restructure(source_title, transcript_text, settings):
            calls_at.append(asyncio.get_running_loop().time())
            return await original(source_title, transcript_text, settings)

        client.restructure = recording_restructure  # type: ignore[method-assign]
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )
        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            await asyncio.sleep(0.2)
            # Wake events must not bypass the usage-limit pause.
            state.llm_wake_event.set()
            for video_id in ("vid-limit-a", "vid-limit-b"):
                await _wait_for_video_status(
                    db, video_id=video_id, expected="done", timeout_seconds=8
                )
            return calls_at
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    calls_at = asyncio.run(_run())
    # Both videos start together; the rate-limited one is retried only after the pause.
    assert len(calls_at) == 3
    assert calls_at[2] - calls_at[0] >= backoff - 0.05


def test_llm_worker_provider_switch_lifts_rate_limit_pause(tmp_path, monkeypatch) -> None:
    import app.workers.llm_worker as llm_worker

    monkeypatch.setattr(llm_worker, "LLM_RATE_LIMITED_SLEEP_SECONDS", 600.0)
    monkeypatch.setattr(llm_worker, "llm_min_call_interval_seconds", lambda _provider: 0.0)
    db_path = tmp_path / "worker-runtime-claude-rate-limit-switch.db"

    async def _run() -> int:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_llm_pending_videos(db, ("vid-limit-switch",))
        await settings_repo.set_llm_settings(db, provider_primary="claude")
        await db.commit()

        client = RateLimitedThenSuccessClient()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )
        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            deadline = asyncio.get_running_loop().time() + 3
            while client.calls < 1:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.05)
            await settings_repo.set_llm_settings(db, provider_primary="codex")
            await _wait_for_video_status(
                db, video_id="vid-limit-switch", expected="done", timeout_seconds=10
            )
            return client.calls
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    assert asyncio.run(_run()) == 2


def test_llm_worker_keeps_rate_limit_pause_for_last_pending_job(tmp_path, monkeypatch) -> None:
    import app.workers.llm_worker as llm_worker

    backoff = 1.0
    monkeypatch.setattr(llm_worker, "LLM_RATE_LIMITED_SLEEP_SECONDS", backoff)
    monkeypatch.setattr(llm_worker, "llm_min_call_interval_seconds", lambda _provider: 0.0)
    db_path = tmp_path / "worker-runtime-claude-rate-limit-last.db"

    class SlowRateLimitedClient(RateLimitedThenSuccessClient):
        async def restructure(self, source_title: str, transcript_text: str, settings):
            if self.calls == 0:
                # Long enough for the loop to see no pending work while it runs.
                await asyncio.sleep(0.3)
            return await super().restructure(source_title, transcript_text, settings)

    async def _run() -> tuple[float, list[float]]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_llm_pending_videos(db, ("vid-limit-last",))
        await settings_repo.set_llm_settings(db, provider_primary="claude")
        await db.commit()

        client = SlowRateLimitedClient()
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )
        started = asyncio.get_running_loop().time()
        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            await _wait_for_video_status(
                db, video_id="vid-limit-last", expected="done", timeout_seconds=8
            )
            return started, client.started_at
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    started, succeeded_at = asyncio.run(_run())
    # The rate-limited call ends at ~0.3s; the retry must wait the full pause after it.
    assert len(succeeded_at) == 1
    assert succeeded_at[0] - started >= 0.3 + backoff - 0.05


def test_llm_worker_ignores_auth_failure_from_previous_provider_job(tmp_path, monkeypatch) -> None:
    import app.workers.llm_worker as llm_worker

    monkeypatch.setattr(llm_worker, "llm_min_call_interval_seconds", lambda _provider: 0.0)
    db_path = tmp_path / "worker-runtime-stale-provider-auth.db"

    class SwitchDuringJobClient(TimedSuccessClient):
        def __init__(self, db) -> None:
            super().__init__()
            self.db = db
            self.calls = 0

        async def restructure(self, source_title: str, transcript_text: str, settings):
            self.calls += 1
            if self.calls == 1:
                # The user switches provider while the cursor job is still running.
                await settings_repo.set_llm_settings(self.db, provider_primary="claude")
                raise LlmClientError(
                    "llm_provider_auth_required",
                    "Authentication required",
                    provider="cursor",
                    retryable=False,
                )
            return await super().restructure(source_title, transcript_text, settings)

    async def _run() -> tuple[int, dict]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_llm_pending_videos(db, ("vid-stale-auth",))
        await settings_repo.set_llm_settings(db, provider_primary="cursor")
        await db.commit()

        client = SwitchDuringJobClient(db)
        state = SimpleNamespace(
            db=db,
            config=AppConfig(max_retry_count=3),
            llm_client=client,
            notification_queue=asyncio.Queue(),
            llm_wake_event=asyncio.Event(),
        )
        task = asyncio.create_task(run_llm_queue_worker(state))
        try:
            await _wait_for_video_status(
                db, video_id="vid-stale-auth", expected="done", timeout_seconds=8
            )
            issue = await llm_repo.get_llm_runtime_issue(db)
            return client.calls, dict(issue)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    calls, issue = asyncio.run(_run())
    assert calls == 2
    assert str(issue.get("code") or "") == ""
