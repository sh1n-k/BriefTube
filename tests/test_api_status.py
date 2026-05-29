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
            (
                "UCunknown001",
                "Unknown Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCunknown001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "vid-unknown-001",
                "UCunknown001",
                "Unknown Video",
                "2026-02-20T00:00:00+00:00",
                "mystery_state",
            ),
        )
        conn.commit()

    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown_count"] == 1


def _seed_queue_video(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    pipeline_status: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (
            "UCqueue-clear-001",
            "Queue Clear Channel",
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCqueue-clear-001",
        ),
    )
    conn.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            video_id,
            "UCqueue-clear-001",
            video_id,
            "2026-02-20T00:00:00+00:00",
            pipeline_status,
        ),
    )


def test_queue_clear_transcript_section_keeps_processing(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_queue_video(conn, video_id="vid-t-pending", pipeline_status="transcript_pending")
        _seed_queue_video(
            conn, video_id="vid-t-processing", pipeline_status="transcript_processing"
        )
        _seed_queue_video(conn, video_id="vid-t-failed", pipeline_status="transcript_failed")
        _seed_queue_video(conn, video_id="vid-t-no-sub", pipeline_status="no_subtitle")
        conn.commit()

    response = client.post("/api/queue/transcript/clear")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "section": "transcript", "cleared_count": 3}
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT video_id, pipeline_status FROM videos ORDER BY video_id"
        ).fetchall()
    assert dict(rows) == {
        "vid-t-failed": "auto_paused",
        "vid-t-no-sub": "auto_paused",
        "vid-t-pending": "auto_paused",
        "vid-t-processing": "transcript_processing",
    }


def test_queue_clear_llm_section_keeps_processing(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_queue_video(conn, video_id="vid-l-pending", pipeline_status="llm_pending")
        _seed_queue_video(conn, video_id="vid-l-processing", pipeline_status="llm_processing")
        _seed_queue_video(conn, video_id="vid-l-failed", pipeline_status="llm_failed")
        _seed_queue_video(conn, video_id="vid-l-review", pipeline_status="manual_review")
        conn.commit()

    response = client.post("/api/queue/llm/clear")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "section": "llm", "cleared_count": 3}
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT video_id, pipeline_status FROM videos ORDER BY video_id"
        ).fetchall()
    assert dict(rows) == {
        "vid-l-failed": "transcript_done",
        "vid-l-pending": "transcript_done",
        "vid-l-processing": "llm_processing",
        "vid-l-review": "transcript_done",
    }


def test_queue_page_renders_section_clear_controls(client: TestClient) -> None:
    response = client.get("/queue")

    assert response.status_code == 200
    assert 'data-queue-clear-section="transcript"' in response.text
    assert 'data-queue-clear-section="llm"' in response.text
    assert "/static/js/ui/queue-status.js" in response.text
    assert "진행 중인 Transcript 작업은 유지" in response.text
