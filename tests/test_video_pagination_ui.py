from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_videos(total: int) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCpage001",
                "Pagination Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCpage001",
            ),
        )

        for index in range(total):
            video_id = f"vid-page-{index:03d}"
            upload_time = f"2026-02-{(index % 28) + 1:02d}T00:00:00+00:00"
            conn.execute(
                """
                INSERT INTO videos(
                    video_id, channel_id, title, upload_time,
                    transcript_status, restructure_status, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    "UCpage001",
                    f"Pagination Video {index:03d}",
                    upload_time,
                    "pending",
                    "pending",
                    0,
                ),
            )
        conn.commit()


def test_home_respects_query_page_and_limit(client: TestClient) -> None:
    _seed_videos(25)

    response = client.get("/", params={"page": 2, "limit": 10})
    assert response.status_code == 200
    html = response.text

    # Page info is rendered from pagination context.
    assert "페이지 2 / 3" in html


def test_video_list_fragment_has_hx_push_url_for_stateful_paging(client: TestClient) -> None:
    _seed_videos(25)

    response = client.get("/views/video-list", params={"page": 2, "limit": 10})
    assert response.status_code == 200
    html = response.text

    assert 'hx-push-url="?page=1&limit=10' in html
    assert 'hx-push-url="?page=3&limit=10' in html
    assert 'hx-push-url="?page=10&limit=10' not in html
    assert 'name="page"' in html
    assert 'name="limit" value="10"' in html
    assert "맨 앞" in html
    assert "맨 뒤" in html


def test_home_uses_default_videos_per_page_when_limit_is_missing(client: TestClient) -> None:
    _seed_videos(25)
    response = client.get("/")
    assert response.status_code == 200
    # default videos_per_page=8 => total_pages=4
    assert "페이지 1 / 4" in response.text
