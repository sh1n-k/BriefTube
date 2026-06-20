from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient

CHANNEL_ID = "UCqueuepage001"


def _seed_queue_rows() -> None:
    db_path = os.environ["DB_PATH"]
    statuses = [
        ("queue-tp1", "transcript_pending"),
        ("queue-tp2", "transcript_pending"),
        ("queue-tpr1", "transcript_processing"),
        ("queue-tf1", "transcript_failed"),
        ("queue-lp1", "llm_pending"),
        ("queue-lp2", "llm_pending"),
        ("queue-lpr1", "llm_processing"),
        ("queue-lf1", "llm_failed"),
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
                INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
                VALUES (?, ?, ?, '2026-02-10T00:00:00+00:00', ?)
                """,
                (video_id, CHANNEL_ID, f"Video {video_id}", status),
            )
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
    assert payload["counts"]["transcript_pending"] == 2
    assert payload["counts"]["transcript_processing"] == 1
    assert payload["counts"]["transcript_failed"] == 1
    assert payload["counts"]["llm_pending"] == 2
    assert payload["counts"]["llm_processing"] == 1
    assert payload["counts"]["llm_failed"] == 1
    assert payload["badge_count"] == 6
    assert payload["workers"] == {"transcript": True, "llm": True}
    assert payload["transcript_guard"]["breaker_state"] == "closed"


def test_queue_page_renders_empty_js_contract_anchors(client: TestClient) -> None:
    response = client.get("/queue")

    assert response.status_code == 200
    html = response.text
    assert "data-queue-page" in html
    assert "data-queue-transcript-list" in html
    assert "data-queue-llm-list" in html
    assert "data-queue-transcript-count" in html
    assert "data-queue-llm-count" in html
