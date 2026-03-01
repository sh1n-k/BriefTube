from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def test_queue_status_keys_and_defaults(client: TestClient) -> None:
    response = client.get("/api/status")
    assert response.status_code == 200

    payload = response.json()
    for key in (
        "auto_paused",
        "transcript_pending",
        "transcript_processing",
        "transcript_done",
        "transcript_failed",
        "no_subtitle",
        "llm_pending",
        "llm_processing",
        "llm_failed",
        "manual_review",
        "done",
        "unknown_count",
    ):
        assert key in payload
        assert isinstance(payload[key], int)

    assert payload["transcript_pending"] == 0
    assert payload["transcript_processing"] == 0
    assert payload["auto_paused"] == 0
    assert payload["transcript_done"] == 0
    assert payload["transcript_failed"] == 0
    assert payload["no_subtitle"] == 0
    assert payload["llm_pending"] == 0
    assert payload["llm_processing"] == 0
    assert payload["llm_failed"] == 0
    assert payload["manual_review"] == 0
    assert payload["done"] == 0
    assert payload["unknown_count"] == 0


def test_queue_status_counts_unknown_values_in_unknown_count(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCunknown001", "Unknown Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCunknown001"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("vid-unknown-001", "UCunknown001", "Unknown Video", "2026-02-20T00:00:00+00:00", "mystery_state"),
        )
        conn.commit()

    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown_count"] == 1
