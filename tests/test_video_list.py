from __future__ import annotations

import json
import os
import re
import sqlite3
from html import unescape
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.domains.downloads import service as downloads_service

FRAGMENT_HEADERS = {"HX-Request": "true"}


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


def _seed_paginated_videos(total: int) -> None:
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
            conn.execute(
                """
                INSERT INTO videos(
                    video_id, channel_id, title, upload_time,
                    pipeline_status, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"vid-page-{index:03d}",
                    "UCpage001",
                    f"Pagination Video {index:03d}",
                    f"2026-02-{(index % 28) + 1:02d}T00:00:00+00:00",
                    "done",
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


def _seed_video_outputs(video_id: str, *, transcript: bool = True, article: bool = True) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        if transcript:
            conn.execute(
                """
                INSERT INTO transcripts(video_id, raw_text, language, source_type)
                VALUES (?, ?, ?, ?)
                """,
                (video_id, f"Transcript for {video_id}", "ko", "auto"),
            )
        if article:
            conn.execute(
                """
                INSERT INTO articles(
                    video_id,
                    title,
                    lead,
                    body,
                    fact_box,
                    timestamps,
                    llm_provider,
                    llm_model,
                    llm_reasoning_effort,
                    llm_generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    f"Article for {video_id}",
                    "Lead paragraph",
                    "Body text",
                    "{}",
                    "[]",
                    "unknown",
                    "",
                    "",
                    "2026-02-10T00:00:00+00:00",
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


def test_video_delete_selected_invalidates_retention_notice_cache(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UC_RET_VIDEO",
                "Retention Video Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UC_RET_VIDEO",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            ("vid-ret-delete", "UC_RET_VIDEO", "old", "2000-01-01T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'done')
            """,
            ("vid-ret-keep", "UC_RET_VIDEO", "new", "2999-01-01T00:00:00+00:00"),
        )
        conn.commit()

    before = client.get("/")
    assert before.status_code == 200
    assert "data-retention-notice" in before.text

    response = client.post(
        "/views/videos/delete-selected",
        data={"video_id": ["vid-ret-delete"], "_page": "1", "_limit": "20"},
    )
    assert response.status_code == 200

    after = client.get("/")
    assert after.status_code == 200
    assert "data-retention-notice" not in after.text


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

    response = client.get(
        "/views/video-list",
        params={"channel_id": "UC_BBB", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    html = response.text
    assert "vid-b-000" in html
    assert "vid-b-001" in html
    assert "vid-a-000" not in html


def test_video_list_and_detail_ignore_tombstoned_outputs(client: TestClient) -> None:
    _seed_channels_and_videos()
    _seed_video_outputs("vid-a-000")
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE transcripts SET deleted_at = datetime('now') WHERE video_id = ?", ("vid-a-000",)
        )
        conn.execute(
            "UPDATE articles SET deleted_at = datetime('now') WHERE video_id = ?", ("vid-a-000",)
        )
        conn.commit()

    list_response = client.get(
        "/views/video-list",
        params={"limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert list_response.status_code == 200
    list_html = list_response.text
    assert "vid-a-000" in list_html
    assert "Transcript for vid-a-000" not in list_html
    assert "Article for vid-a-000" not in list_html

    detail_response = client.get("/videos/vid-a-000")
    assert detail_response.status_code == 200
    assert "Transcript for vid-a-000" not in detail_response.text
    assert "Article for vid-a-000" not in detail_response.text


def test_video_list_channel_name_links_to_channel_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE videos SET pipeline_status = 'done' WHERE video_id = 'vid-b-000'")
        conn.commit()

    response = client.get(
        "/views/video-list",
        params={"limit": "20", "pipeline_status": "done"},
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    html = unescape(response.text)
    expected_query = (
        "page=1&limit=20&sort=upload_time&order=desc&channel_id=UC_BBB&pipeline_status=done"
    )
    assert re.search(r">\s*Channel B\s*</a>", html)
    assert f'href="/?{expected_query}"' in html
    assert f'hx-get="/views/video-list?{expected_query}"' in html
    assert f'hx-push-url="?{expected_query}"' in html


def test_video_list_pipeline_status_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE videos SET pipeline_status = 'done' WHERE video_id = 'vid-b-000'")
        conn.execute(
            "UPDATE videos SET pipeline_status = 'manual_review' WHERE video_id = 'vid-a-001'"
        )
        conn.commit()

    response = client.get(
        "/views/video-list",
        params={"pipeline_status": "done", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    html = response.text
    assert 'name="pipeline_status"' in html
    assert "전체 상태" in html
    assert "vid-b-000" in html
    assert "vid-a-001" not in html
    assert "vid-b-001" not in html


def test_video_list_filters_apply_as_and(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO categories(name, sort_order, is_default) VALUES (?, ?, 0)", ("Tech", 999)
        )
        category_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE channels SET category_id = ? WHERE channel_id = ?", (category_id, "UC_BBB")
        )
        conn.execute(
            "UPDATE videos SET pipeline_status = 'manual_review' WHERE video_id IN ('vid-a-000', 'vid-b-001')"
        )
        conn.execute("UPDATE videos SET pipeline_status = 'done' WHERE video_id = 'vid-b-000'")
        conn.commit()

    response = client.get(
        "/views/video-list",
        params={
            "category_id": str(category_id),
            "channel_id": "UC_BBB",
            "pipeline_status": "manual_review",
            "limit": "20",
        },
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    html = response.text
    assert "vid-b-001" in html
    assert "vid-b-000" not in html
    assert "vid-a-000" not in html


def test_video_list_sets_home_push_url_header(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get(
        "/views/video-list",
        params={
            "channel_id": "UC_BBB",
            "page": "2",
            "limit": "20",
            "sort": "upload_time",
            "order": "desc",
        },
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    push_url = response.headers.get("HX-Push-Url")
    assert push_url is not None
    assert push_url.startswith("/?")
    assert "/views/video-list" not in push_url
    assert "channel_id=UC_BBB" in push_url


def test_video_list_sets_home_push_url_header_with_pipeline_status(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE videos SET pipeline_status = 'done' WHERE video_id = 'vid-b-000'")
        conn.commit()

    response = client.get(
        "/views/video-list",
        params={
            "pipeline_status": "done",
            "page": "1",
            "limit": "20",
            "sort": "upload_time",
            "order": "desc",
        },
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    push_url = response.headers.get("HX-Push-Url")
    assert push_url is not None
    assert "pipeline_status=done" in push_url


def test_video_list_direct_access_redirects_to_home_page(client: TestClient) -> None:
    response = client.get(
        "/views/video-list?page=2&limit=10&pipeline_status=done",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.path == "/"
    assert parse_qs(location.query) == {
        "page": ["2"],
        "limit": ["10"],
        "pipeline_status": ["done"],
    }


def test_home_respects_query_page_and_limit(client: TestClient) -> None:
    _seed_paginated_videos(25)

    response = client.get("/", params={"page": 2, "limit": 10})

    assert response.status_code == 200
    assert "페이지 2 / 3" in response.text


def test_video_list_fragment_has_hx_push_url_for_stateful_paging(client: TestClient) -> None:
    _seed_paginated_videos(25)

    response = client.get(
        "/views/video-list",
        params={"page": 2, "limit": 10},
        headers=FRAGMENT_HEADERS,
    )

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
    _seed_paginated_videos(25)

    response = client.get("/")

    assert response.status_code == 200
    assert "페이지 1 / 4" in response.text


def test_video_list_fragment_keeps_pipeline_status_in_paging_queries(client: TestClient) -> None:
    _seed_paginated_videos(25)

    response = client.get(
        "/views/video-list",
        params={"page": 2, "limit": 10, "pipeline_status": "done"},
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    html = response.text
    assert "pipeline_status=done" in html
    assert 'name="pipeline_status" value="done"' in html


def test_video_list_accepts_empty_category_id_as_all(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get(
        "/views/video-list",
        params={"category_id": "", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    html = response.text
    assert "Channel A" in html
    assert "Channel B" in html


def test_home_accepts_empty_category_id_as_all(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/", params={"category_id": "", "limit": "20"})
    assert response.status_code == 200
    html = response.text
    assert "Channel A" in html
    assert "Channel B" in html


def test_home_pipeline_status_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE videos SET pipeline_status = 'done' WHERE video_id = 'vid-b-001'")
        conn.commit()

    response = client.get("/", params={"pipeline_status": "done", "limit": "20"})
    assert response.status_code == 200
    html = response.text
    assert "vid-b-001" in html
    assert "vid-a-000" not in html


def test_video_list_category_then_all_restores_all_channel_options(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO categories(name, sort_order, is_default) VALUES (?, ?, 0)", ("Tech", 999)
        )
        category_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE channels SET category_id = ? WHERE channel_id = ?", (category_id, "UC_BBB")
        )
        conn.commit()

    filtered = client.get(
        "/views/video-list",
        params={"category_id": str(category_id), "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert filtered.status_code == 200
    assert "Channel B" in filtered.text
    assert "Channel A" not in filtered.text

    restored = client.get(
        "/views/video-list",
        params={"category_id": "", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert restored.status_code == 200
    assert "Channel A" in restored.text
    assert "Channel B" in restored.text


def test_video_list_sort_order(client: TestClient) -> None:
    """sort=upload_time&order=asc -> 오름차순 정렬"""
    _seed_channels_and_videos()

    response = client.get(
        "/views/video-list",
        params={"sort": "upload_time", "order": "asc", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    html = response.text
    pos_a0 = html.index("vid-a-000")
    pos_b1 = html.index("vid-b-001")
    assert pos_a0 < pos_b1, "Ascending: earlier upload should appear first"


def test_video_list_renders_cdn_thumbnail_for_missing_local_path(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    html = response.text
    assert "https://i.ytimg.com/vi/vid-a-000/hqdefault.jpg" in html
    assert 'loading="lazy"' in html


def test_video_list_renders_download_selected_button(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
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


def test_video_download_selected_preserves_pipeline_status_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE videos SET pipeline_status = 'no_subtitle' WHERE video_id = 'vid-b-001'"
        )
        conn.commit()

    response = client.post(
        "/views/videos/download-selected",
        data={"_page": "1", "_limit": "20", "_pipeline_status": "no_subtitle"},
    )

    assert response.status_code == 200
    assert 'name="_pipeline_status" value="no_subtitle"' in response.text
    assert "vid-b-001" in response.text
    assert "vid-a-000" not in response.text


def test_video_download_selected_duplicate_only_returns_info_tone(
    client: TestClient,
    monkeypatch,
) -> None:
    _seed_channels_and_videos()
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
    call_counter = {"count": 0}
    original_list_videos_by_ids = downloads_service.videos_repo.list_videos_by_ids

    async def wrapped_list_videos_by_ids(db, video_ids):
        call_counter["count"] += 1
        return await original_list_videos_by_ids(db, video_ids)

    async def should_not_be_called(*_args, **_kwargs):
        raise AssertionError("videos_repo.get_video should not be called in bulk download flow")

    monkeypatch.setattr(
        "app.domains.downloads.service.videos_repo.list_videos_by_ids", wrapped_list_videos_by_ids
    )
    monkeypatch.setattr("app.domains.downloads.service.videos_repo.get_video", should_not_be_called)

    response = client.post(
        "/views/videos/download-selected",
        data={"video_id": ["vid-a-000", "vid-b-000"], "_page": "1", "_limit": "20"},
    )

    assert response.status_code == 200
    assert call_counter["count"] == 1


def test_video_list_renders_article_selected_button(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    html = response.text
    assert "data-video-article-request-selected" in html
    assert "/views/videos/article-request-selected" in html
    assert "data-busy-label=" in html


def test_video_list_renders_fixed_action_column(client: TestClient) -> None:
    _seed_channels_and_videos()
    _seed_video_outputs("vid-a-000", transcript=True, article=True)
    _seed_video_outputs("vid-a-001", transcript=True, article=False)

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)

    assert response.status_code == 200
    html = response.text
    assert "data-video-row-actions" in html
    assert "data-video-transcript-copy" in html
    assert 'data-transcript-url="/api/videos/vid-a-000/transcript"' in html
    assert "data-video-article-preview-load" in html
    assert 'data-article-preview-url="/views/videos/vid-a-000/article-preview-modal"' in html
    assert "기사 보기" in html or "View article" in html
    assert "data-video-bulk-article-count" in html
    assert "data-video-select-eligible" in html
    assert "data-video-select-has-article" in html
    assert "data-video-select-none" in html
    assert "data-video-selection-sticky" in html
    assert "video-row-action-chip--request" in html or "video-row-action-chip--view" in html
    assert "data-article-eligible=" in html
    assert "vid-a-001" in html

    empty_response = client.get(
        "/views/video-list",
        params={"channel_id": "UC_NO_MATCH", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert empty_response.status_code == 200
    assert "영상이 없습니다" in empty_response.text or "No videos yet" in empty_response.text


def test_video_list_marks_article_eligible_for_transcript_done(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE videos SET pipeline_status = 'transcript_done' WHERE video_id = 'vid-a-001'"
        )
        conn.commit()
    _seed_video_outputs("vid-a-001", transcript=True, article=False)

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    html = response.text
    assert 'value="vid-a-001"' in html
    assert 'data-article-eligible="1"' in html
    assert "data-video-list-inline-article" in html
    assert "기사화" in html or "Article" in html


def test_video_article_preview_modal_fragment_matches_detail_contract(
    client: TestClient,
) -> None:
    _seed_channels_and_videos()
    _seed_video_outputs("vid-a-000", transcript=True, article=True)

    response = client.get(
        "/views/videos/vid-a-000/article-preview-modal",
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    html = response.text
    assert "data-article-preview-modal" in html
    assert "data-article-preview-scroll" in html
    assert "data-article-preview-content" in html
    assert 'data-copy-target="article-copy-source"' in html
    assert "data-video-list-article-modal" in html
    assert "data-article-preview-prev" in html
    assert "data-article-preview-next" in html
    assert "data-article-preview-position" in html
    assert 'aria-keyshortcuts="ArrowLeft j"' in html
    assert 'aria-keyshortcuts="ArrowRight k"' in html
    assert "Article for vid-a-000" in html


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


def test_video_article_selected_preserves_pipeline_status_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE videos SET pipeline_status = 'done' WHERE video_id = 'vid-a-002'")
        conn.commit()

    response = client.post(
        "/views/videos/article-request-selected",
        data={"_page": "1", "_limit": "20", "_pipeline_status": "done"},
    )

    assert response.status_code == 200
    assert 'name="_pipeline_status" value="done"' in response.text
    assert "vid-a-002" in response.text
    assert "vid-b-000" not in response.text


def test_video_article_selected_limit_exceeded_returns_bulk_toast(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UC_MANUAL_LIMIT",
                "Manual Limit Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UC_MANUAL_LIMIT",
            ),
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
