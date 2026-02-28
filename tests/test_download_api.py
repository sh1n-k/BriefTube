from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app import repository
from app.database import init_database, open_database
from app.services.downloads import download_video


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


def _insert_download_job(
    db_path: str,
    *,
    video_id: str = "vid-download-001",
    video_title: str = "Download Video",
    status: str = "succeeded",
    target_dir: str | None = None,
    output_path: str | None = None,
) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO download_jobs(
                video_id,
                video_title,
                status,
                quality,
                overwrite,
                target_dir,
                attempt_count,
                requested_at,
                updated_at,
                finished_at,
                output_path
            )
            VALUES (?, ?, ?, '720', 0, ?, 1, datetime('now'), datetime('now'), datetime('now'), ?)
            """,
            (
                video_id,
                video_title,
                status,
                target_dir,
                output_path,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)


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
    assert response.json()["message_key"] == "download_toast_queued"
    assert response.json()["tone"] == "success"


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


def test_download_file_probe_success_with_job_target_dir(client: TestClient, tmp_path: Path) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_video(db_path)
    custom_dir = tmp_path / "custom-downloads"
    custom_dir.mkdir(parents=True, exist_ok=True)
    filename = "downloaded.mp4"
    (custom_dir / filename).write_bytes(b"ok")
    job_id = _insert_download_job(
        db_path,
        target_dir=str(custom_dir),
        output_path=filename,
    )

    response = client.get(f"/downloads/files/{filename}", params={"job_id": job_id, "probe": True})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["filename"] == filename


def test_download_file_probe_missing_job_returns_code(client: TestClient) -> None:
    response = client.get("/downloads/files/missing.mp4", params={"job_id": 9999, "probe": True})

    assert response.status_code == 404
    assert response.json()["code"] == "download_job_not_found"


def test_download_file_probe_missing_file_returns_code(client: TestClient, tmp_path: Path) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_video(db_path)
    custom_dir = tmp_path / "custom-downloads"
    custom_dir.mkdir(parents=True, exist_ok=True)
    job_id = _insert_download_job(
        db_path,
        target_dir=str(custom_dir),
        output_path="missing.mp4",
    )

    response = client.get("/downloads/files/missing.mp4", params={"job_id": job_id, "probe": True})

    assert response.status_code == 404
    assert response.json()["code"] == "download_file_not_found"


def test_download_video_returns_specific_output_path_error_code(tmp_path: Path) -> None:
    missing_dir = tmp_path / "missing-download-dir"
    result = asyncio.run(
        download_video(
            video_id="vid-download-001",
            quality="1080",
            overwrite=False,
            output_dir=str(missing_dir),
            timeout_seconds=10,
        )
    )

    assert result.ok is False
    assert result.error_code == "download_path_not_found"


def test_recover_stuck_download_jobs_marks_running_failed_and_logs_event(tmp_path: Path) -> None:
    db_path = tmp_path / "recover-downloads.db"

    async def _run() -> tuple[int, str, str, int]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            await db.execute(
                """
                INSERT INTO download_jobs(
                    video_id,
                    video_title,
                    status,
                    quality,
                    overwrite,
                    attempt_count,
                    requested_at,
                    updated_at
                )
                VALUES ('vid-running-1', 'Running Video', 'running', '1080', 0, 1, datetime('now'), datetime('now'))
                """
            )
            await db.execute(
                """
                INSERT INTO download_jobs(
                    video_id,
                    video_title,
                    status,
                    quality,
                    overwrite,
                    attempt_count,
                    requested_at,
                    updated_at
                )
                VALUES ('vid-pending-1', 'Pending Video', 'pending', '1080', 0, 1, datetime('now'), datetime('now'))
                """
            )
            await db.commit()

            recovered_count = await repository.recover_stuck_download_jobs(db)
            running_job = await repository.get_download_job(db, 1)
            event_count_cursor = await db.execute(
                """
                SELECT COUNT(1) AS cnt
                FROM download_events
                WHERE job_id = 1 AND event_type = 'failed' AND error_code = 'worker_interrupted'
                """
            )
            event_count_row = await event_count_cursor.fetchone()
            return (
                recovered_count,
                str((running_job or {}).get("status") or ""),
                str((running_job or {}).get("error_code") or ""),
                int((event_count_row["cnt"] if event_count_row else 0) or 0),
            )
        finally:
            await db.close()

    recovered_count, recovered_status, recovered_error_code, event_count = asyncio.run(_run())
    assert recovered_count == 1
    assert recovered_status == "failed"
    assert recovered_error_code == "worker_interrupted"
    assert event_count == 1
