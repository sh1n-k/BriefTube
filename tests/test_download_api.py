from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient
import pytest


def _seed_video(db_path: str, *, video_id: str = "vid-download-001") -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCdownload001", "Download Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCdownload001"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            (video_id, "UCdownload001", "Download Video", "2026-02-26T00:00:00+00:00"),
        )
        conn.commit()


def _insert_failed_download_job(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO download_jobs(
                video_id,
                video_title,
                status,
                quality,
                overwrite,
                attempt_count,
                requested_at,
                updated_at,
                finished_at,
                error_code,
                error_message
            )
            VALUES (?, ?, 'failed', '720', 0, 1, datetime('now'), datetime('now'), datetime('now'), 'process_failed', 'failed')
            """,
            ("vid-download-001", "Download Video"),
        )
        job_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO download_events(job_id, event_type, error_code)
            VALUES (?, 'failed', 'process_failed')
            """,
            (job_id,),
        )
        conn.commit()


def test_request_video_download_rejects_when_ffmpeg_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_video(db_path)
    monkeypatch.setattr("app.routers.api.is_ffmpeg_available", lambda: False)

    response = client.post(
        "/api/videos/vid-download-001/downloads",
        json={"quality": "1080", "overwrite": False},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ffmpeg_missing"
    assert response.json()["queued"] is False


def test_request_video_download_enqueue_sets_wake_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_video(db_path)

    monkeypatch.setattr("app.routers.api.is_ffmpeg_available", lambda: True)

    expected_target_dir = str(client.app.state.runtime.config.download_dir)

    async def fake_create_download_job(
        db,
        *,
        video_id: str,
        video_title: str,
        quality: str,
        overwrite: bool,
        target_dir: str,
    ):
        assert video_id == "vid-download-001"
        assert video_title == "Download Video"
        assert target_dir == expected_target_dir
        return {
            "created": True,
            "duplicate": False,
            "job": {
                "id": 101,
                "status": "pending",
                "video_id": video_id,
                "quality": quality,
                "overwrite": int(overwrite),
                "target_dir": target_dir,
            },
        }

    monkeypatch.setattr("app.routers.api.repository.create_download_job", fake_create_download_job)

    response = client.post(
        "/api/videos/vid-download-001/downloads",
        json={"quality": "720", "overwrite": True},
    )

    assert response.status_code == 202
    assert response.json()["queued"] is True
    assert response.json()["job_id"] == 101
    assert client.app.state.runtime.download_wake_event.is_set()


def test_settings_download_defaults_update(client: TestClient) -> None:
    expected_target_dir = str(client.app.state.runtime.config.download_dir)
    response = client.put(
        "/api/settings/downloads",
        json={"quality": "720", "overwrite": True, "output_dir": expected_target_dir},
    )
    assert response.status_code == 200
    assert response.json()["download_defaults"] == {
        "quality": "720",
        "overwrite": True,
        "output_dir": expected_target_dir,
    }

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["download_defaults"] == {
        "quality": "720",
        "overwrite": True,
        "output_dir": expected_target_dir,
    }


def test_download_progress_returns_counts_and_events(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_video(db_path)
    _insert_failed_download_job(db_path)

    response = client.get("/api/downloads/progress", params={"after_event_id": 0})

    assert response.status_code == 200
    payload = response.json()
    assert payload["failed_count"] == 1
    assert payload["pending_count"] == 0
    assert payload["running_count"] == 0
    assert payload["active_count"] == 0
    assert len(payload["events"]) == 1
    assert payload["events"][0]["event_type"] == "failed"
