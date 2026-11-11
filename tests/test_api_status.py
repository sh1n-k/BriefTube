from __future__ import annotations

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.helpers.db_seed import seed_video_job_sync

# Legacy pipeline_status -> (job kind, job status, error_code) producing it in the view.
_JOB_BY_LEGACY_STATUS: dict[str, tuple[str, str, str | None]] = {
    "transcript_pending": ("transcript", "queued", None),
    "transcript_processing": ("transcript", "running", None),
    "transcript_failed": ("transcript", "failed", "non_retryable_failure"),
    "no_subtitle": ("transcript", "failed", "no_subtitle"),
    "llm_pending": ("article", "queued", None),
    "llm_processing": ("article", "running", None),
    "llm_failed": ("article", "queued", "llm_provider_timeout"),
    "manual_review": ("article", "failed", "retry_exhausted"),
}


def _seed_legacy_status(conn: sqlite3.Connection, video_id: str, status: str) -> None:
    kind, job_status, error_code = _JOB_BY_LEGACY_STATUS[status]
    if kind == "article":
        # LLM-stage videos always have a transcript.
        conn.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, 'raw', 'ko', 'manual')
            """,
            (video_id,),
        )
    seed_video_job_sync(conn, video_id, kind, status=job_status, error_code=error_code)


def _statuses(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(
        conn.execute(
            "SELECT video_id, pipeline_status FROM video_pipeline_v ORDER BY video_id"
        ).fetchall()
    )


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


def test_queue_status_cannot_hold_unknown_job_states(client: TestClient) -> None:
    # Status is derived from constrained job rows, so an unknown state cannot be stored.
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
            INSERT INTO videos(video_id, channel_id, title, upload_time)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-unknown-001", "UCunknown001", "Unknown Video", "2026-02-20T00:00:00+00:00"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            seed_video_job_sync(conn, "vid-unknown-001", "transcript", status="mystery_state")
        conn.commit()

    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown_count"] == 0
    assert payload["auto_paused"] == 1


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
        for video_id, status in rows:
            conn.execute(
                """
                INSERT INTO videos(video_id, channel_id, title, upload_time)
                VALUES (?, 'UCqueueactive001', ?, '2026-02-20T00:00:00+00:00')
                """,
                (video_id, video_id),
            )
            _seed_legacy_status(conn, video_id, status)
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
        INSERT INTO videos(video_id, channel_id, title, upload_time)
        VALUES (?, ?, ?, ?)
        """,
        (video_id, "UCqueue-clear-001", video_id, "2026-02-20T00:00:00+00:00"),
    )
    _seed_legacy_status(conn, video_id, pipeline_status)


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
        rows = _statuses(conn)
    assert rows == {
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
        rows = _statuses(conn)
    assert rows == {
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
        rows = _statuses(conn)
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
        rows = _statuses(conn)
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
