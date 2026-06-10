from __future__ import annotations

from app.main import _resolve_worker_start_specs
from tests.e2e.conftest import _build_e2e_env


def _task_names(enabled_worker_names: set[str]) -> list[str]:
    return [spec.task_name for spec in _resolve_worker_start_specs(enabled_worker_names)]


def test_worker_start_specs_include_enabled_workers_in_start_order() -> None:
    names = _task_names(
        {
            "rss",
            "channel_metadata",
            "download",
            "manual_transcript",
            "transcript",
            "manual_article",
            "llm",
            "notifier",
        }
    )

    assert names == [
        "rss_poller",
        "channel_metadata_worker",
        "download_worker",
        "manual_transcript_worker",
        "transcript_fetcher",
        "manual_article_worker",
        "llm_queue_worker",
        "telegram_notifier",
    ]
    assert _task_names({"rss", "download", "llm"}) == [
        "rss_poller",
        "download_worker",
        "llm_queue_worker",
    ]


def test_worker_start_specs_preserve_sparse_insert_order() -> None:
    assert _task_names({"rss", "download", "transcript", "llm", "notifier"}) == [
        "rss_poller",
        "download_worker",
        "llm_queue_worker",
        "transcript_fetcher",
        "telegram_notifier",
    ]


def test_e2e_server_env_disables_background_workers(tmp_path) -> None:
    env = _build_e2e_env(
        db_path=str(tmp_path / "e2e.db"),
        thumbnail_dir=str(tmp_path / "thumbs"),
        download_dir=str(tmp_path / "downloads"),
        log_dir=str(tmp_path / "logs"),
    )

    for name in (
        "RSS",
        "DOWNLOAD",
        "MANUAL_ARTICLE",
        "LLM",
        "NOTIFIER",
        "REMOTE_SYNC",
        "CHANNEL_METADATA",
        "TRANSCRIPT",
        "MANUAL_TRANSCRIPT",
    ):
        assert env[f"BRIEFTUBE_DISABLE_{name}_WORKER"] == "1"
    assert env["BRIEFTUBE_REMOTE_SYNC_ENABLED"] == "0"
    assert env["BRIEFTUBE_REMOTE_SYNC_DSN"] == ""
