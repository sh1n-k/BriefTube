from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_channels_and_videos() -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UC_AAA", "Channel A", "https://www.youtube.com/feeds/videos.xml?channel_id=UC_AAA"),
        )
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UC_BBB", "Channel B", "https://www.youtube.com/feeds/videos.xml?channel_id=UC_BBB"),
        )
        for i in range(3):
            conn.execute(
                """
                INSERT INTO videos(
                    video_id, channel_id, title, upload_time,
                    pipeline_status, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"vid-a-{i:03d}",
                    "UC_AAA",
                    f"Video A-{i}",
                    f"2026-02-{10 + i:02d}T00:00:00+00:00",
                    "transcript_pending",
                    0,
                ),
            )
        for i in range(2):
            conn.execute(
                """
                INSERT INTO videos(
                    video_id, channel_id, title, upload_time,
                    pipeline_status, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"vid-b-{i:03d}",
                    "UC_BBB",
                    f"Video B-{i}",
                    f"2026-02-{20 + i:02d}T00:00:00+00:00",
                    "transcript_pending",
                    0,
                ),
            )
        conn.commit()


def test_video_delete_selected(client: TestClient) -> None:
    """체크박스 선택 삭제 -> 해당 영상 목록에서 제거"""
    _seed_channels_and_videos()

    response = client.post(
        "/views/videos/delete-selected",
        data={"video_id": ["vid-a-000", "vid-a-001"], "_page": "1", "_limit": "20"},
    )
    assert response.status_code == 200
    html = response.text
    assert "vid-a-000" not in html
    assert "vid-a-001" not in html
    assert "vid-a-002" in html
    assert "vid-b-000" in html


def test_video_delete_empty_selection(client: TestClient) -> None:
    """선택 없이 삭제 -> 목록 그대로 반환, 에러 없음"""
    _seed_channels_and_videos()

    response = client.post(
        "/views/videos/delete-selected",
        data={"_page": "1", "_limit": "20"},
    )
    assert response.status_code == 200
    html = response.text
    assert "vid-a-000" in html
    assert "vid-b-001" in html


def test_video_list_channel_filter(client: TestClient) -> None:
    """channel_id 파라미터 -> 해당 채널 영상만 반환"""
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"channel_id": "UC_BBB", "limit": "20"})
    assert response.status_code == 200
    html = response.text
    assert "vid-b-000" in html
    assert "vid-b-001" in html
    assert "vid-a-000" not in html


def test_video_list_sort_order(client: TestClient) -> None:
    """sort=upload_time&order=asc -> 오름차순 정렬"""
    _seed_channels_and_videos()

    response = client.get(
        "/views/video-list",
        params={"sort": "upload_time", "order": "asc", "limit": "20"},
    )
    assert response.status_code == 200
    html = response.text
    pos_a0 = html.index("vid-a-000")
    pos_b1 = html.index("vid-b-001")
    assert pos_a0 < pos_b1, "Ascending: earlier upload should appear first"


def test_video_list_renders_cdn_thumbnail_for_missing_local_path(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"limit": "20"})
    assert response.status_code == 200
    html = response.text
    assert "https://i.ytimg.com/vi/vid-a-000/hqdefault.jpg" in html
    assert 'loading="lazy"' in html
