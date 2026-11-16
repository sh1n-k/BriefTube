from __future__ import annotations

import json
import os
import sqlite3

from fastapi.testclient import TestClient

from app.i18n import get_texts

TXT = get_texts("ko")

FRAGMENT_HEADERS = {"HX-Request": "true"}


def _seed_video(
    *,
    video_id: str = "vid-manual-transcript-001",
    pipeline_status: str = "paused",
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
            INSERT INTO videos(video_id, channel_id, title, upload_time, target_stage)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                video_id,
                "UCmanualtx001",
                f"Manual Transcript {video_id}",
                "2026-03-15T00:00:00+00:00",
                "none" if pipeline_status == "paused" else "article",
            ),
        )
        if pipeline_status == "article_ready":
            conn.execute(
                """
                INSERT INTO articles(video_id, title, lead, body)
                VALUES (?, 'Done article', 'lead', 'body')
                """,
                (video_id,),
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


def test_manual_transcript_request_view_queues_paused_video(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-api-001")

    client.app.state.runtime.transcript_wake_event.clear()
    response = client.post("/views/videos/vid-manual-tx-api-001/transcript-request")

    assert response.status_code == 200
    toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast.get("tone") == "success"
    assert "자막 수집 요청" in str(toast.get("message") or "")
    assert client.app.state.runtime.transcript_wake_event.is_set() is True

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT video_id, status, origin
            FROM video_jobs
            WHERE video_id = ? AND kind = 'transcript'
            """,
            ("vid-manual-tx-api-001",),
        ).fetchone()

    assert row is not None
    assert row[0] == "vid-manual-tx-api-001"
    assert row[1] == "queued"
    assert row[2] == "manual"


def test_manual_transcript_request_view_rejects_invalid_status(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-api-002", pipeline_status="article_ready")

    response = client.post("/views/videos/vid-manual-tx-api-002/transcript-request")

    assert response.status_code == 200
    toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast == {"message": TXT["video_transcript_request_invalid_status"], "tone": "error"}

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(1) FROM video_jobs WHERE video_id = ? AND kind = 'transcript'",
            ("vid-manual-tx-api-002",),
        ).fetchone()
    assert count is not None
    assert int(count[0] or 0) == 0


def test_manual_transcript_request_view_skips_duplicate_active_job(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-api-003")

    first = client.post("/views/videos/vid-manual-tx-api-003/transcript-request")
    second = client.post("/views/videos/vid-manual-tx-api-003/transcript-request")

    assert first.status_code == 200
    assert second.status_code == 200
    toast = json.loads(second.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast == {"message": TXT["video_transcript_request_duplicate"], "tone": "info"}

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(1) FROM video_jobs WHERE video_id = ? AND kind = 'transcript'",
            ("vid-manual-tx-api-003",),
        ).fetchone()

    assert count is not None
    assert int(count[0] or 0) == 1


def test_manual_transcript_request_view_respects_permission_guard(
    client: TestClient,
    monkeypatch,
) -> None:
    _seed_video(video_id="vid-manual-tx-api-004")
    monkeypatch.setenv("BRIEFTUBE_DISABLE_MANUAL_TRANSCRIPT_REQUESTS", "1")

    response = client.post("/views/videos/vid-manual-tx-api-004/transcript-request")

    assert response.status_code == 403
    toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast.get("tone") == "error"


def test_manual_transcript_detail_ui_for_paused_video(client: TestClient) -> None:
    _seed_video(video_id="vid-manual-tx-ui-001")

    response = client.get("/videos/vid-manual-tx-ui-001")

    assert response.status_code == 200
    assert 'data-pipeline-status="paused"' in response.text
    assert "data-video-transcript-request-button" in response.text
    assert "/views/videos/vid-manual-tx-ui-001/transcript-request" in response.text
    assert "자막 수집" in response.text


def test_manual_transcript_detail_ui_shows_active_state_and_auto_refresh(
    client: TestClient,
) -> None:
    _seed_video(video_id="vid-manual-tx-ui-002")
    client.post("/views/videos/vid-manual-tx-ui-002/transcript-request")

    response = client.get(
        "/views/videos/vid-manual-tx-ui-002/dynamic-fragment",
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    assert 'data-manual-transcript-status="pending"' in response.text
    assert 'data-video-detail-auto-refresh="1"' in response.text


def _transcript_jobs(video_id: str) -> list[tuple[str, str]]:
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        return conn.execute(
            "SELECT status, origin FROM video_jobs WHERE video_id = ? AND kind = 'transcript'",
            (video_id,),
        ).fetchall()


def test_transcript_refetch_view_queues_video_with_transcript(client: TestClient) -> None:
    _seed_video(video_id="vid-refetch-api-001", with_transcript=True)
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        conn.execute(
            "UPDATE videos SET transcript_target_language = 'en-US' WHERE video_id = ?",
            ("vid-refetch-api-001",),
        )
        conn.commit()

    client.app.state.runtime.transcript_wake_event.clear()
    response = client.post("/views/videos/vid-refetch-api-001/transcript-refetch")

    assert response.status_code == 200
    toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast == {"message": TXT["video_transcript_refetch_queued"], "tone": "success"}
    assert client.app.state.runtime.transcript_wake_event.is_set() is True
    assert _transcript_jobs("vid-refetch-api-001") == [("queued", "manual")]
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        language = conn.execute(
            "SELECT transcript_target_language FROM videos WHERE video_id = ?",
            ("vid-refetch-api-001",),
        ).fetchone()[0]
        raw_text = conn.execute(
            "SELECT raw_text FROM transcripts WHERE video_id = ?",
            ("vid-refetch-api-001",),
        ).fetchone()[0]
    assert language is None
    assert raw_text == "existing transcript"


def test_transcript_refetch_view_rejects_video_without_transcript(client: TestClient) -> None:
    _seed_video(video_id="vid-refetch-api-002")

    response = client.post("/views/videos/vid-refetch-api-002/transcript-refetch")

    toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast == {"message": TXT["video_transcript_refetch_no_transcript"], "tone": "error"}
    assert _transcript_jobs("vid-refetch-api-002") == []


def test_transcript_refetch_view_skips_when_job_is_active(client: TestClient) -> None:
    _seed_video(video_id="vid-refetch-api-003", with_transcript=True)

    client.post("/views/videos/vid-refetch-api-003/transcript-refetch")
    second = client.post("/views/videos/vid-refetch-api-003/transcript-refetch")

    toast = json.loads(second.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast == {"message": TXT["video_transcript_refetch_busy"], "tone": "info"}
    assert _transcript_jobs("vid-refetch-api-003") == [("queued", "manual")]


def test_transcript_refetch_view_respects_permission_guard(
    client: TestClient,
    monkeypatch,
) -> None:
    _seed_video(video_id="vid-refetch-api-004", with_transcript=True)
    monkeypatch.setenv("BRIEFTUBE_DISABLE_MANUAL_TRANSCRIPT_REQUESTS", "1")

    response = client.post("/views/videos/vid-refetch-api-004/transcript-refetch")

    assert response.status_code == 403
    assert _transcript_jobs("vid-refetch-api-004") == []


def test_transcript_refetch_button_shows_only_for_idle_video_with_transcript(
    client: TestClient,
) -> None:
    _seed_video(
        video_id="vid-refetch-ui-001", pipeline_status="article_ready", with_transcript=True
    )
    _seed_video(video_id="vid-refetch-ui-002")

    with_transcript = client.get("/videos/vid-refetch-ui-001")
    without_transcript = client.get("/videos/vid-refetch-ui-002")
    client.post("/views/videos/vid-refetch-ui-001/transcript-refetch")
    active = client.get(
        "/views/videos/vid-refetch-ui-001/dynamic-fragment", headers=FRAGMENT_HEADERS
    )

    assert "/views/videos/vid-refetch-ui-001/transcript-refetch" in with_transcript.text
    assert TXT["detail_transcript_refetch_confirm_with_article"] in with_transcript.text
    assert "data-video-transcript-refetch-button" not in without_transcript.text
    assert "data-video-transcript-refetch-button" not in active.text
