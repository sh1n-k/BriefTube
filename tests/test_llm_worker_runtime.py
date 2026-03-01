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


def test_llm_worker_requeues_pending_when_auth_is_required(tmp_path) -> None:
    db_path = tmp_path / "worker-runtime.db"

    async def _run() -> tuple[str, int, int, str | None]:
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
            return str(video["pipeline_status"]), int(video["retry_count"]), alert_count, sent_key
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()

    status, retry_count, alert_count, sent_key = asyncio.run(_run())
    assert status == "llm_pending"
    assert retry_count == 2
    assert alert_count >= 1
    assert sent_key == "1"
