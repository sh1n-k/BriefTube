from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def test_queue_status_keys_and_defaults(client: TestClient) -> None:
    response = client.get("/api/status")
    assert response.status_code == 200

    payload = response.json()
    expected_keys = {
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
    }
    assert expected_keys <= payload.keys()
    assert all(isinstance(payload[key], int) for key in expected_keys)
    assert all(payload[key] == 0 for key in expected_keys)


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


def test_queue_status_and_poll_count_active_videos(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCqueueactive001",
                "Queue Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCqueueactive001",
            ),
        )
        rows = [
            ("vid-active-tp", "transcript_pending"),
            ("vid-active-lp", "llm_pending"),
        ]
        conn.executemany(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, 'UCqueueactive001', ?, '2026-02-20T00:00:00+00:00', ?)
            """,
            [(video_id, video_id, status) for video_id, status in rows],
        )
        conn.commit()

    status_payload = client.get("/api/status").json()
    assert status_payload["transcript_pending"] == 1
    assert status_payload["llm_pending"] == 1
    assert status_payload["unknown_count"] == 0

    poll_response = client.get("/api/queue/poll")
    assert poll_response.status_code == 200
    poll_payload = poll_response.json()
    assert poll_payload["counts"]["transcript_pending"] == 1
    assert poll_payload["counts"]["llm_pending"] == 1
    assert poll_payload["badge_count"] == 2
    assert "vid-active-tp" in poll_payload["queue_html"]
    assert "vid-active-lp" in poll_payload["queue_html"]


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


def test_queue_retry_failed_transcript_section(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_queue_video(conn, video_id="vid-t-pending", pipeline_status="transcript_pending")
        _seed_queue_video(conn, video_id="vid-t-failed", pipeline_status="transcript_failed")
        _seed_queue_video(conn, video_id="vid-t-no-sub", pipeline_status="no_subtitle")
        conn.commit()

    response = client.post("/api/queue/transcript/retry-failed")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "section": "transcript", "retried_count": 2}
    with sqlite3.connect(db_path) as conn:
        rows = dict(
            conn.execute(
                "SELECT video_id, pipeline_status FROM videos ORDER BY video_id"
            ).fetchall()
        )
    assert rows["vid-t-failed"] == "transcript_pending"
    assert rows["vid-t-no-sub"] == "transcript_pending"
    assert rows["vid-t-pending"] == "transcript_pending"


def test_queue_retry_failed_llm_section(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_queue_video(conn, video_id="vid-l-pending", pipeline_status="llm_pending")
        _seed_queue_video(conn, video_id="vid-l-failed", pipeline_status="llm_failed")
        _seed_queue_video(conn, video_id="vid-l-review", pipeline_status="manual_review")
        conn.commit()

    response = client.post("/api/queue/llm/retry-failed")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "section": "llm", "retried_count": 2}
    with sqlite3.connect(db_path) as conn:
        rows = dict(
            conn.execute(
                "SELECT video_id, pipeline_status FROM videos ORDER BY video_id"
            ).fetchall()
        )
    assert rows["vid-l-failed"] == "llm_pending"
    assert rows["vid-l-review"] == "llm_pending"
    assert rows["vid-l-pending"] == "llm_pending"


def test_queue_page_renders_retry_all_when_failed_exists(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _seed_queue_video(conn, video_id="vid-t-failed", pipeline_status="transcript_failed")
        _seed_queue_video(conn, video_id="vid-l-failed", pipeline_status="llm_failed")
        conn.commit()

    response = client.get("/queue")
    assert response.status_code == 200
    assert 'data-queue-retry-section="transcript"' in response.text
    assert 'data-queue-retry-section="llm"' in response.text
    assert "/static/js/ui/queue-status.js" in response.text
    assert "진행 중인 Transcript 작업은 유지" in response.text
