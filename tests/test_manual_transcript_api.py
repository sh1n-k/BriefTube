from __future__ import annotations

import json
import os
import sqlite3

from fastapi.testclient import TestClient

FRAGMENT_HEADERS = {"HX-Request": "true"}


def _seed_video(
    *,
    video_id: str = "vid-manual-transcript-001",
    pipeline_status: str = "auto_paused",
    with_transcript: bool = False,
) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCmanualtx001",
                "Manual Transcript Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCmanualtx001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, processing_stage_snapshot)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                "UCmanualtx001",
                f"Manual Transcript {video_id}",
                "2026-03-15T00:00:00+00:00",
                pipeline_status,
                "off" if pipeline_status == "auto_paused" else "full",
            ),
        )
        if with_transcript:
            conn.execute(
                """
                INSERT INTO transcripts(video_id, raw_text, language, source_type)
                VALUES (?, ?, ?, ?)
                """,
                (video_id, "existing transcript", "ko", "manual"),
            )
        conn.commit()


def test_manual_transcript_request_api_queues_auto_paused_video(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-api-001")

    response = client.post("/api/videos/vid-manual-tx-api-001/transcript-request")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "queued"
    assert payload["job_id"] is not None

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT video_id, status FROM manual_transcript_jobs WHERE video_id = ?",
            ("vid-manual-tx-api-001",),
        ).fetchone()

    assert row is not None
    assert row[0] == "vid-manual-tx-api-001"
    assert row[1] == "pending"


def test_manual_transcript_request_api_rejects_invalid_status(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-api-002", pipeline_status="done")

    response = client.post("/api/videos/vid-manual-tx-api-002/transcript-request")

    assert response.status_code == 409
    assert "pipeline_status:done" in response.text


def test_manual_transcript_request_api_skips_duplicate_active_job(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-api-003")

    first = client.post("/api/videos/vid-manual-tx-api-003/transcript-request")
    second = client.post("/api/videos/vid-manual-tx-api-003/transcript-request")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "skipped"
    assert second.json()["reason"] == "active_job_exists"

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(1) FROM manual_transcript_jobs WHERE video_id = ?",
            ("vid-manual-tx-api-003",),
        ).fetchone()

    assert count is not None
    assert int(count[0] or 0) == 1


def test_manual_transcript_request_api_respects_permission_guard(
    client: TestClient,
    monkeypatch,
) -> None:
    _seed_video(video_id="vid-manual-tx-api-004")
    monkeypatch.setenv("BRIEFTUBE_DISABLE_MANUAL_TRANSCRIPT_REQUESTS", "1")

    response = client.post("/api/videos/vid-manual-tx-api-004/transcript-request")

    assert response.status_code == 403


def test_manual_transcript_detail_ui_for_auto_paused_video(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-ui-001")

    response = client.get("/videos/vid-manual-tx-ui-001")

    assert response.status_code == 200
    assert "data-video-transcript-request-button" in response.text
    assert "/views/videos/vid-manual-tx-ui-001/transcript-request" in response.text
    assert "자막 수집" in response.text


def test_manual_transcript_detail_ui_shows_active_state_and_auto_refresh(
    client: TestClient,
) -> None:
    _seed_video(video_id="vid-manual-tx-ui-002")
    client.post("/api/videos/vid-manual-tx-ui-002/transcript-request")

    response = client.get(
        "/views/videos/vid-manual-tx-ui-002/dynamic-fragment",
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    assert 'data-manual-transcript-status="pending"' in response.text
    assert 'data-video-detail-auto-refresh="1"' in response.text


def test_manual_transcript_view_endpoint_returns_toast_header(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-view-001")

    response = client.post("/views/videos/vid-manual-tx-view-001/transcript-request")

    assert response.status_code == 200
    payload = json.loads(response.headers.get("HX-Trigger", "{}"))
    toast = payload.get("video-transcript-request-toast", {})
    assert toast.get("tone") == "success"
    assert "자막 수집 요청" in str(toast.get("message") or "")
