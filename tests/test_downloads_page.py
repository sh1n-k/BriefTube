from __future__ import annotations

import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

FRAGMENT_HEADERS = {"HX-Request": "true"}

LONG_ERROR = (
    "this is a very long failure message to verify one-line summary truncation "
    "and readability improvements in download history"
)
LONG_OUTPUT = (
    "very-long-output-file-name-for-download-history-view-"
    "with-multiple-segments-and-identifiers.mp4"
)
DOWNLOAD_TARGET_DIR = "/tmp/brieftube-downloads"


def _seed_download_jobs(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCdownload-view-1",
                "View Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCdownload-view-1",
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            (
                "vid-download-view-1",
                "UCdownload-view-1",
                "View Video 1",
                "2026-02-26T00:00:00+00:00",
            ),
        )
        conn.execute(
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
                output_path,
                file_size_bytes,
                target_dir,
                error_code,
                error_message
            )
            VALUES
            (?, ?, 'failed', '1080', 0, 1, datetime('now'), datetime('now'), datetime('now'), NULL, NULL, NULL, 'process_failed', ?),
            (?, ?, 'succeeded', '720', 1, 1, datetime('now'), datetime('now'), datetime('now'), ?, 12345, ?, NULL, NULL)
            """,
            (
                "vid-download-view-1",
                "View Video 1",
                LONG_ERROR,
                "vid-download-view-1",
                "View Video 1",
                LONG_OUTPUT,
                DOWNLOAD_TARGET_DIR,
            ),
        )
        conn.commit()


def _seed_pending_download_job(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO download_jobs(video_id, video_title, status, quality, overwrite)
            VALUES (?, ?, 'pending', '1080', 0)
            """,
            ("vid-download-view-pending", "Pending View Video"),
        )
        conn.commit()


def test_downloads_page_renders_failed_status_with_retry(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)
    monkeypatch.setattr("app.routers.pages_downloads.is_ffmpeg_available", lambda: False)

    response = client.get("/downloads?status=failed")

    assert response.status_code == 200
    html = response.text
    assert "다운로드" in html
    assert "ffmpeg가 설치되지 않았습니다." in html
    assert "data-download-retry-button" in html
    assert "data-download-detail-open" in html
    assert "View Video 1" in html
    assert "실패" in html


def test_downloads_page_summary_and_mobile_card_contract(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)

    response = client.get("/downloads?status=all")

    assert response.status_code == 200
    html = response.text
    assert "data-download-detail-open" in html
    assert 'data-video-id="vid-download-view-1"' in html
    assert "data-error-message=" in html
    assert "data-output-url=" in html
    assert "data-output-full-path=" in html
    assert f'data-output-full-path="{DOWNLOAD_TARGET_DIR}/{LONG_OUTPUT}"' in html
    assert 'id="download-history-fragment"' in html
    assert (
        'data-download-history-refresh-url="/views/downloads/table?status=all&amp;page=1"' in html
    )


def test_downloads_page_status_filter_limits_rows(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)
    _seed_pending_download_job(db_path)

    response = client.get("/views/downloads/table?status=pending", headers=FRAGMENT_HEADERS)

    assert response.status_code == 200
    html = response.text
    assert "대기" in html
    assert "Pending View Video" in html
    assert "View Video 1" not in html


def test_downloads_page_empty_state_for_missing_filter(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)

    response = client.get("/views/downloads/table?status=running", headers=FRAGMENT_HEADERS)

    assert response.status_code == 200
    assert "다운로드 이력이 없습니다." in response.text


def test_download_history_fragment_view_returns_partial_markup(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)

    response = client.get(
        "/views/downloads/table?status=failed&page=1",
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    html = response.text
    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert 'id="download-history-fragment"' in html
    assert "View Video 1" in html
    assert (
        'data-download-history-refresh-url="/views/downloads/table?status=failed&amp;page=1"'
        in html
    )


def test_download_history_clear_removes_terminal_jobs_only(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO download_jobs(video_id, video_title, status, quality, overwrite)
            VALUES (?, ?, 'pending', '1080', 0)
            """,
            ("vid-download-view-1", "View Video 1"),
        )
        conn.commit()

    response = client.post("/views/downloads/clear", data={"status": "all"})

    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    trigger = json.loads(response.headers["HX-Trigger"])
    html = response.text
    assert "View Video 1" in html
    assert trigger["video-download-bulk-toast"]["tone"] == "success"
    assert "2" in trigger["video-download-bulk-toast"]["message"]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT status FROM download_jobs ORDER BY status").fetchall()
    assert [row[0] for row in rows] == ["pending"]


def test_download_history_clear_pending_filter_is_noop(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)

    response = client.post("/views/downloads/clear", data={"status": "pending"})

    assert response.status_code == 200
    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["video-download-bulk-toast"]["tone"] == "info"
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(1) FROM download_jobs").fetchone()[0]
    assert count == 2


def test_download_history_clear_rejects_invalid_status(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)

    response = client.post("/views/downloads/clear", data={"status": "invalid"})

    assert response.status_code == 400
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(1) FROM download_jobs").fetchone()[0]
    assert count == 2
