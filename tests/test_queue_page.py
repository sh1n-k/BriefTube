from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient

from tests.helpers.db_seed import seed_video_job_sync

CHANNEL_ID = "UCqueuepage001"

# Seed state -> (job kind, job status, error_code) producing it in the view.
_JOB_BY_SEED_STATE: dict[str, tuple[str, str, str | None]] = {
    "transcript_pending": ("transcript", "queued", None),
    "transcript_running": ("transcript", "running", None),
    "transcript_failed": ("transcript", "failed", "non_retryable_failure"),
    "article_pending": ("article", "queued", None),
    "article_running": ("article", "running", None),
    "article_retry_wait": ("article", "queued", "llm_provider_timeout"),
}


def _seed_queue_rows() -> None:
    db_path = os.environ["DB_PATH"]
    statuses = [
        ("queue-tp1", "transcript_pending"),
        ("queue-tp2", "transcript_pending"),
        ("queue-tpr1", "transcript_running"),
        ("queue-tf1", "transcript_failed"),
        ("queue-lp1", "article_pending"),
        ("queue-lp2", "article_pending"),
        ("queue-lpr1", "article_running"),
        ("queue-lf1", "article_retry_wait"),
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                CHANNEL_ID,
                "Queue Page Channel",
                f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}",
            ),
        )
        for video_id, status in statuses:
            conn.execute(
                """
                INSERT INTO videos(video_id, channel_id, title, upload_time)
                VALUES (?, ?, ?, '2026-02-10T00:00:00+00:00')
                """,
                (video_id, CHANNEL_ID, f"Video {video_id}"),
            )
            kind, job_status, error_code = _JOB_BY_SEED_STATE[status]
            if kind == "article":
                conn.execute(
                    "INSERT INTO transcripts(video_id, raw_text, language, source_type) "
                    "VALUES (?, 'raw', 'ko', 'manual')",
                    (video_id,),
                )
            seed_video_job_sync(conn, video_id, kind, status=job_status, error_code=error_code)
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value, updated_at) VALUES ('worker_transcript_enabled', 'true', datetime('now'))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value, updated_at) VALUES ('worker_llm_enabled', 'true', datetime('now'))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value, updated_at) VALUES ('transcript_guard_breaker_state', 'closed', datetime('now'))"
        )
        conn.commit()


def test_queue_page_renders_js_contract_anchors(client: TestClient) -> None:
    _seed_queue_rows()

    response = client.get("/queue")

    assert response.status_code == 200
    html = response.text
    for marker in (
        "data-queue-page",
        "data-queue-content",
        "data-queue-transcript-list",
        "data-queue-llm-list",
        "data-queue-transcript-count",
        "data-queue-llm-count",
        "data-queue-transcript-worker-indicator",
        "data-queue-llm-worker-indicator",
        "data-queue-guard-indicator",
    ):
        assert marker in html


def test_queue_poll_api_returns_counts_and_runtime_state(client: TestClient) -> None:
    _seed_queue_rows()

    response = client.get("/api/queue/poll")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["transcript_items"]) == 4
    assert len(payload["llm_items"]) == 4
    counts = payload["counts"]
    assert counts["transcript_queued"] == 2
    assert counts["transcript_running"] == 1
    assert counts["transcript_failed"] == 1
    assert counts["transcript_pending"] == 3
    assert counts["article_queued"] == 2
    assert counts["article_running"] == 1
    assert counts["article_retry_wait"] == 1
    assert counts["article_pending"] == 4
    assert payload["badge_count"] == 6
    html = payload["queue_html"]
    assert 'data-queue-retry-transcript="queue-tf1"' in html
    assert 'data-queue-retry-llm="queue-lf1"' in html
    assert 'data-queue-retry-llm="queue-lp1"' not in html
    assert "non_retryable_failure" in html
    assert "llm_provider_timeout" in html
    assert 'data-pipeline-activity="retry_wait"' in html
    assert payload["workers"] == {"transcript": True, "llm": True}
    assert payload["transcript_guard"]["breaker_state"] == "closed"
