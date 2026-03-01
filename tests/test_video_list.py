from __future__ import annotations

import json
import os
import sqlite3

from fastapi.testclient import TestClient

from app.routers import views as views_router


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


def _seed_pending_download_job(video_id: str, *, target_dir: str) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
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
                updated_at
            )
            VALUES (?, ?, 'pending', '1080', 0, ?, 1, datetime('now'), datetime('now'))
            """,
            (video_id, f"Pending {video_id}", target_dir),
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


def test_video_list_renders_download_selected_button(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"limit": "20"})
    assert response.status_code == 200
    assert "data-video-download-selected" in response.text
    assert "data-busy-label=" in response.text


def test_video_download_selected_empty_selection_returns_bulk_toast(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.post(
        "/views/videos/download-selected",
        data={"_page": "1", "_limit": "20"},
    )

    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    assert "video-download-bulk-toast" in response.headers["HX-Trigger"]


def test_video_download_selected_duplicate_only_returns_info_tone(
    client: TestClient,
    monkeypatch,
) -> None:
    _seed_channels_and_videos()
    monkeypatch.setattr("app.routers.views.is_ffmpeg_available", lambda: True)
    _seed_pending_download_job(
        "vid-a-000",
        target_dir=str(client.app.state.runtime.config.download_dir),
    )

    response = client.post(
        "/views/videos/download-selected",
        data={"video_id": ["vid-a-000"], "_page": "1", "_limit": "20"},
    )

    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["video-download-bulk-toast"]["tone"] == "info"


def test_video_download_selected_uses_single_batch_query(client: TestClient, monkeypatch) -> None:
    _seed_channels_and_videos()
    monkeypatch.setattr("app.routers.views.is_ffmpeg_available", lambda: True)

    call_counter = {"count": 0}
    original_list_videos_by_ids = views_router.repository.list_videos_by_ids

    async def wrapped_list_videos_by_ids(db, video_ids):
        call_counter["count"] += 1
        return await original_list_videos_by_ids(db, video_ids)

    async def should_not_be_called(*_args, **_kwargs):
        raise AssertionError("repository.get_video should not be called in bulk download flow")

    monkeypatch.setattr("app.routers.views.repository.list_videos_by_ids", wrapped_list_videos_by_ids)
    monkeypatch.setattr("app.routers.views.repository.get_video", should_not_be_called)

    response = client.post(
        "/views/videos/download-selected",
        data={"video_id": ["vid-a-000", "vid-b-000"], "_page": "1", "_limit": "20"},
    )

    assert response.status_code == 200
    assert call_counter["count"] == 1


def test_video_list_renders_article_selected_button(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"limit": "20"})
    assert response.status_code == 200
    html = response.text
    assert "data-video-article-request-selected" in html
    assert "/views/videos/article-request-selected" in html
    assert "data-busy-label=" in html


def test_video_article_selected_empty_selection_returns_bulk_toast(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.post(
        "/views/videos/article-request-selected",
        data={"_page": "1", "_limit": "20"},
    )

    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert "video-article-request-toast" in payload


def test_video_article_selected_limit_exceeded_returns_bulk_toast(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            ("UC_MANUAL_LIMIT", "Manual Limit Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UC_MANUAL_LIMIT"),
        )
        for idx in range(11):
            conn.execute(
                """
                INSERT INTO videos(
                    video_id, channel_id, title, upload_time,
                    pipeline_status, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"vid-manual-limit-{idx:02d}",
                    "UC_MANUAL_LIMIT",
                    f"Manual Limit {idx}",
                    f"2026-02-{10 + idx:02d}T00:00:00+00:00",
                    "done",
                    0,
                ),
            )
        conn.commit()

    response = client.post(
        "/views/videos/article-request-selected",
        data={
            "video_id": [f"vid-manual-limit-{idx:02d}" for idx in range(11)],
            "_page": "1",
            "_limit": "20",
        },
    )

    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    toast = payload["video-article-request-toast"]
    assert "11" in str(toast.get("message", ""))
    assert "10" in str(toast.get("message", ""))
