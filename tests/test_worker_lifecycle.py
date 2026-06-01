from __future__ import annotations

from app.main import _resolve_worker_start_specs


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
