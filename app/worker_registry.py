from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from app.state import AppState
from app.workers.channel_metadata_worker import run_channel_metadata_worker
from app.workers.download_worker import run_download_worker
from app.workers.llm_worker import run_llm_queue_worker
from app.workers.manual_article_worker import run_manual_article_worker
from app.workers.manual_transcript_worker import run_manual_transcript_worker
from app.workers.notifier_worker import run_telegram_notifier
from app.workers.poller import run_rss_poller
from app.workers.transcript_worker import run_transcript_fetcher

WorkerFactory = Callable[[AppState], Coroutine[Any, Any, None]]


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    worker_name: str
    task_name: str
    factory: WorkerFactory
    order: int
    test_allow_alias: str | None = None
    insert_at: int | None = None

    @property
    def disable_env_name(self) -> str:
        return f"BRIEFTUBE_DISABLE_{self.worker_name.upper()}_WORKER"

    @property
    def test_enable_env_name(self) -> str:
        return f"BRIEFTUBE_ENABLE_{self.worker_name.upper()}_WORKER_IN_TESTS"


WORKER_SPECS: tuple[WorkerSpec, ...] = (
    WorkerSpec("rss", "rss_poller", run_rss_poller, order=10),
    WorkerSpec("download", "download_worker", run_download_worker, order=20),
    WorkerSpec("manual_article", "manual_article_worker", run_manual_article_worker, order=30),
    WorkerSpec("llm", "llm_queue_worker", run_llm_queue_worker, order=40),
    WorkerSpec("notifier", "telegram_notifier", run_telegram_notifier, order=50),
    WorkerSpec(
        "channel_metadata",
        "channel_metadata_worker",
        run_channel_metadata_worker,
        order=60,
        test_allow_alias="BRIEFTUBE_ENABLE_METADATA_WORKER_IN_TESTS",
        insert_at=1,
    ),
    WorkerSpec("transcript", "transcript_fetcher", run_transcript_fetcher, order=70, insert_at=3),
    WorkerSpec(
        "manual_transcript",
        "manual_transcript_worker",
        run_manual_transcript_worker,
        order=80,
        insert_at=3,
    ),
)
