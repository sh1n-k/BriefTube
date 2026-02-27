from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_retention_data(db_path: str) -> None:
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(days=200)).isoformat()
    recent_time = (now - timedelta(days=10)).isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UCret001", "Retention Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UCret001"),
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


def test_retention_page_shows_expired_only(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_retention_data(db_path)

    response = client.get("/retention")
    assert response.status_code == 200
    html = response.text
    assert "vid-ret-old-001" in html
    assert "vid-ret-new-001" not in html
    assert 'data-retention-select-all' not in html
    assert 'data-retention-select-toggle' in html
    assert 'data-retention-select-item' in html
    assert 'w-28 whitespace-nowrap' in html
    assert 'w-52 whitespace-nowrap p-3 text-left' in html
    assert 'w-52 whitespace-nowrap p-3 text-slate-600' in html

    home = client.get("/")
    assert home.status_code == 200
    assert "보관 알림" in home.text
    assert "data-retention-notice" in home.text
    assert "fixed bottom-4 right-4" in home.text
    assert "data-retention-notice-dismiss" in home.text
    assert "setTimeout(dismiss, 7000);" in home.text


def test_retention_delete_selected(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_retention_data(db_path)

    response = client.post(
        "/retention/delete-selected",
        data={"video_id": "vid-ret-old-001"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/retention?deleted=")

    with sqlite3.connect(db_path) as conn:
        old_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-old-001'").fetchone()
        new_row = conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-ret-new-001'").fetchone()
    assert old_row is None
    assert new_row is not None
