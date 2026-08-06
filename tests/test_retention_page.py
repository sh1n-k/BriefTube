from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


def _seed_retention_data(db_path: str) -> None:
    now = datetime.now(UTC)
    old_time = (now - timedelta(days=200)).isoformat()
    recent_time = (now - timedelta(days=10)).isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCret001",
                "Retention Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCret001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            ("vid-ret-old-001", "UCret001", "old", old_time),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            ("vid-ret-new-001", "UCret001", "new", recent_time),
        )
        conn.commit()


def _seed_many_expired_videos(db_path: str, *, count: int) -> None:
    now = datetime.now(UTC)
    old_time = (now - timedelta(days=200)).isoformat()
    recent_time = (now - timedelta(days=10)).isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCretbulk001",
                "Retention Bulk Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCretbulk001",
            ),
        )
        conn.executemany(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            [
                (f"vid-ret-bulk-{idx:03d}", "UCretbulk001", f"old {idx}", old_time)
                for idx in range(count)
            ],
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            ("vid-ret-bulk-fresh", "UCretbulk001", "fresh", recent_time),
        )
        conn.commit()


def test_retention_page_shows_expired_only(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_retention_data(db_path)

    response = client.get("/retention")
    assert response.status_code == 200
    html = response.text
    assert "vid-ret-old-001" in html
    assert "vid-ret-new-001" not in html
    assert "data-retention-select-all" not in html
    assert "data-retention-select-toggle" in html
    assert "data-retention-select-item" in html

    home = client.get("/")
    assert home.status_code == 200
    assert "보관 알림" in home.text
    assert "data-retention-notice" in home.text
    assert "data-retention-notice-dismiss" in home.text
    assert "/static/js/main-ui.js" in home.text


def test_retention_page_ignores_deleted_expired_videos(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_retention_data(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM videos WHERE video_id = 'vid-ret-old-001'")
        conn.commit()

    response = client.get("/retention")
    assert response.status_code == 200
    assert "vid-ret-old-001" not in response.text

    home = client.get("/")
    assert home.status_code == 200
    assert "data-retention-notice" not in home.text


def test_retention_page_limits_visible_rows_but_shows_total(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_many_expired_videos(db_path, count=101)

    response = client.get("/retention")
    assert response.status_code == 200
    assert response.text.count("data-retention-select-item") == 100
    assert "101" in response.text
    assert "/retention?page=2" in response.text


def test_retention_delete_all_requires_confirmation(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_retention_data(db_path)

    # confirm_delete_all 없이 POST → 삭제 안 됨, /retention으로 리다이렉트
    response = client.post(
        "/retention/delete-all",
        data={},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/retention"

    with sqlite3.connect(db_path) as conn:
        old_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-old-001'").fetchone()
    assert old_row is not None


def test_retention_delete_all_with_confirmation(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_retention_data(db_path)

    before = client.get("/")
    assert before.status_code == 200
    assert "data-retention-notice" in before.text

    # confirm_delete_all=on POST → 만료 영상 삭제됨
    response = client.post(
        "/retention/delete-all",
        data={"confirm_delete_all": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "retention-content" in response.text

    with sqlite3.connect(db_path) as conn:
        old_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-old-001'").fetchone()
        new_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-new-001'").fetchone()
    assert old_row is None
    assert new_row is not None

    after = client.get("/")
    assert after.status_code == 200
    assert "data-retention-notice" not in after.text


def test_retention_delete_all_batches_expired_videos(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_many_expired_videos(db_path, count=505)

    response = client.post(
        "/retention/delete-all",
        data={"confirm_delete_all": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        expired_count = conn.execute(
            """
            SELECT COUNT(1)
            FROM videos
            WHERE channel_id = 'UCretbulk001'
              AND video_id != 'vid-ret-bulk-fresh'
            """
        ).fetchone()[0]
        fresh_row = conn.execute(
            "SELECT 1 FROM videos WHERE video_id = 'vid-ret-bulk-fresh'"
        ).fetchone()
    assert expired_count == 0
    assert fresh_row is not None


def test_retention_delete_selected(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_retention_data(db_path)

    response = client.post(
        "/retention/delete-selected",
        data={"video_id": "vid-ret-old-001"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "retention-content" in response.text

    with sqlite3.connect(db_path) as conn:
        old_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-old-001'").fetchone()
        new_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-new-001'").fetchone()
    assert old_row is None
    assert new_row is not None


def test_retention_delete_selected_handles_large_manual_payload(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_retention_data(db_path)

    response = client.post(
        "/retention/delete-selected",
        data={"video_id": [*[f"missing-{idx}" for idx in range(1200)], "vid-ret-old-001"]},
        follow_redirects=False,
    )
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        old_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-old-001'").fetchone()
        new_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-new-001'").fetchone()
    assert old_row is None
    assert new_row is not None


def test_retention_delete_selected_batches_many_expired_videos(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_many_expired_videos(db_path, count=505)

    response = client.post(
        "/retention/delete-selected",
        data={"video_id": [f"vid-ret-bulk-{idx:03d}" for idx in range(505)]},
        follow_redirects=False,
    )
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        remaining = conn.execute(
            """
            SELECT COUNT(1)
            FROM videos
            WHERE channel_id = 'UCretbulk001'
              AND video_id != 'vid-ret-bulk-fresh'
            """
        ).fetchone()[0]
    assert remaining == 0
