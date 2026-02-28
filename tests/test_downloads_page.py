from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient
import pytest

LONG_ERROR = (
    "this is a very long failure message to verify one-line summary truncation "
    "and readability improvements in download history"
)
LONG_OUTPUT = (
    "very-long-output-file-name-for-download-history-view-"
    "with-multiple-segments-and-identifiers.mp4"
)


def _seed_download_jobs(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCdownload-view-1", "View Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCdownload-view-1"),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            ("vid-download-view-1", "UCdownload-view-1", "View Video 1", "2026-02-26T00:00:00+00:00"),
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
                error_code,
                error_message
            )
            VALUES
            (?, ?, 'failed', '1080', 0, 1, datetime('now'), datetime('now'), datetime('now'), NULL, NULL, 'process_failed', ?),
            (?, ?, 'succeeded', '720', 1, 1, datetime('now'), datetime('now'), datetime('now'), ?, 12345, NULL, NULL)
            """,
            (
                "vid-download-view-1",
                "View Video 1",
                LONG_ERROR,
                "vid-download-view-1",
                "View Video 1",
                LONG_OUTPUT,
            ),
        )
        conn.commit()


def test_downloads_page_renders_failed_status_with_retry(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_download_jobs(db_path)
    monkeypatch.setattr("app.routers.pages.is_ffmpeg_available", lambda: False)

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
    assert "table-auto" in html
    assert "md:hidden" in html
    assert "overflow-x-auto" not in html
    assert "hidden rounded-lg border border-slate-200 bg-white shadow-sm md:block" in html
    assert "process_failed · this is a very long fai..." in html
    assert "very-long-output-file-name-for-download-..." in html
    assert "data-download-detail-open" in html
    assert 'data-video-id="vid-download-view-1"' in html
    assert "data-error-message=" in html
    assert "data-output-url=" in html
