from __future__ import annotations

import json
import os
import re
import sqlite3
from html import unescape
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.domains.downloads import service as downloads_service
from tests.helpers.db_seed import seed_video_job_sync

FRAGMENT_HEADERS = {"HX-Request": "true"}


def _set_status(
    conn: sqlite3.Connection,
    video_id: str,
    status: str,
    *,
    attempt_count: int = 0,
    error_message: str | None = None,
) -> None:
    """Reproduce a pipeline status (plus activity) with facts and video_jobs rows.

    Seed states: paused, transcript_ready, article_ready, or a job-backed state from
    ``jobs`` below (``*_running`` / ``article_retry_wait`` add the activity).
    """
    conn.execute("DELETE FROM video_jobs WHERE video_id = ?", (video_id,))
    if status == "article_ready":
        conn.execute(
            """
            INSERT OR IGNORE INTO articles(video_id, title, lead, body)
            VALUES (?, ?, 'Lead paragraph', 'Body text')
            """,
            (video_id, f"Article for {video_id}"),
        )
        return
    if status == "transcript_ready":
        # target_stage='article' without an article job would derive "paused".
        conn.execute(
            "UPDATE videos SET target_stage = 'transcript' WHERE video_id = ?", (video_id,)
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, 'ko', 'auto')
            """,
            (video_id, f"Transcript for {video_id}"),
        )
        return
    if status == "paused":
        return
    jobs = {
        "transcript_pending": ("transcript", "queued", None),
        "transcript_running": ("transcript", "running", None),
        "transcript_failed": ("transcript", "failed", "non_retryable_failure"),
        "no_subtitle": ("transcript", "failed", "no_subtitle"),
        "article_pending": ("article", "queued", None),
        "article_running": ("article", "running", None),
        "article_retry_wait": ("article", "queued", "llm_provider_timeout"),
        "article_failed": ("article", "failed", "retry_exhausted"),
    }
    kind, job_status, error_code = jobs[status]
    seed_video_job_sync(
        conn,
        video_id,
        kind,
        status=job_status,
        attempt_count=attempt_count,
        error_code=error_code,
        error_message=error_message if error_code else None,
    )


def _insert_video(
    conn: sqlite3.Connection,
    video_id: str,
    channel_id: str,
    title: str,
    upload_time: str,
    status: str,
) -> None:
    conn.execute(
        "INSERT INTO videos(video_id, channel_id, title, upload_time) VALUES (?, ?, ?, ?)",
        (video_id, channel_id, title, upload_time),
    )
    _set_status(conn, video_id, status)


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
            _insert_video(
                conn,
                f"vid-a-{i:03d}",
                "UC_AAA",
                f"Video A-{i}",
                f"2026-02-{10 + i:02d}T00:00:00+00:00",
                "transcript_pending",
            )
        for i in range(2):
            _insert_video(
                conn,
                f"vid-b-{i:03d}",
                "UC_BBB",
                f"Video B-{i}",
                f"2026-02-{20 + i:02d}T00:00:00+00:00",
                "transcript_pending",
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
            _insert_video(
                conn,
                f"vid-page-{index:03d}",
                "UCpage001",
                f"Pagination Video {index:03d}",
                f"2026-02-{(index % 28) + 1:02d}T00:00:00+00:00",
                "article_ready",
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
                INSERT OR REPLACE INTO transcripts(video_id, raw_text, language, source_type)
                VALUES (?, ?, ?, ?)
                """,
                (video_id, f"Transcript for {video_id}", "ko", "auto"),
            )
        if article:
            conn.execute(
                """
                INSERT OR REPLACE INTO articles(
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
        _insert_video(
            conn,
            "vid-ret-delete",
            "UC_RET_VIDEO",
            "old",
            "2000-01-01T00:00:00+00:00",
            "article_ready",
        )
        _insert_video(
            conn,
            "vid-ret-keep",
            "UC_RET_VIDEO",
            "new",
            "2999-01-01T00:00:00+00:00",
            "article_ready",
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


def test_video_list_channel_name_links_to_channel_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _set_status(conn, "vid-b-000", "article_ready")
        conn.commit()

    response = client.get(
        "/views/video-list",
        params={"limit": "20", "status": "article_ready"},
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    html = unescape(response.text)
    expected_query = (
        "page=1&limit=20&sort=upload_time&order=desc&channel_id=UC_BBB&status=article_ready"
    )
    assert re.search(r">\s*Channel B\s*</a>", html)
    assert f'href="/?{expected_query}"' in html
    assert f'hx-get="/views/video-list?{expected_query}"' in html
    assert f'hx-push-url="?{expected_query}"' in html


def test_video_list_status_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _set_status(conn, "vid-b-000", "article_ready")
        _set_status(conn, "vid-a-001", "article_failed")
        conn.commit()

    response = client.get(
        "/views/video-list",
        params={"status": "article_ready", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    html = response.text
    assert 'name="status"' in html
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
        _set_status(conn, "vid-a-000", "article_failed")
        _set_status(conn, "vid-b-001", "article_failed")
        _set_status(conn, "vid-b-000", "article_ready")
        conn.commit()

    response = client.get(
        "/views/video-list",
        params={
            "category_id": str(category_id),
            "channel_id": "UC_BBB",
            "status": "article_failed",
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


def test_video_list_sets_home_push_url_header_with_status(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _set_status(conn, "vid-b-000", "article_ready")
        conn.commit()

    response = client.get(
        "/views/video-list",
        params={
            "status": "article_ready",
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
    assert "status=article_ready" in push_url


def test_video_list_direct_access_redirects_to_home_page(client: TestClient) -> None:
    response = client.get(
        "/views/video-list?page=2&limit=10&status=article_ready",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.path == "/"
    assert parse_qs(location.query) == {
        "page": ["2"],
        "limit": ["10"],
        "status": ["article_ready"],
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
    assert "처음" in html or "First" in html
    assert "끝" in html or "Last" in html


def test_home_uses_default_videos_per_page_when_limit_is_missing(client: TestClient) -> None:
    _seed_paginated_videos(25)

    response = client.get("/")

    assert response.status_code == 200
    assert "페이지 1 / 4" in response.text


def test_video_list_fragment_keeps_status_in_paging_queries(client: TestClient) -> None:
    _seed_paginated_videos(25)

    response = client.get(
        "/views/video-list",
        params={"page": 2, "limit": 10, "status": "article_ready"},
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    html = response.text
    assert "status=article_ready" in html
    assert 'name="status" value="article_ready"' in html


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


def test_home_status_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _set_status(conn, "vid-b-001", "article_ready")
        conn.commit()

    response = client.get("/", params={"status": "article_ready", "limit": "20"})
    assert response.status_code == 200
    html = response.text
    assert "vid-b-001" in html
    assert "vid-a-000" not in html


def test_legacy_pipeline_status_query_maps_to_status_filter(client: TestClient) -> None:
    """북마크된 ?pipeline_status= 홈 주소는 새 ?status= 주소로 리다이렉트된다."""
    _seed_channels_and_videos()
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        _set_status(conn, "vid-a-000", "article_failed")
        _set_status(conn, "vid-a-001", "article_retry_wait")
        _set_status(conn, "vid-b-000", "article_ready")
        conn.commit()

    redirect = client.get(
        "/",
        params={"pipeline_status": "manual_review", "limit": "20"},
        follow_redirects=False,
    )
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/?limit=20&status=article_failed"
    unknown_redirect = client.get("/", params={"pipeline_status": "bogus"}, follow_redirects=False)
    assert unknown_redirect.headers["location"] == "/"

    home = client.get("/", params={"pipeline_status": "manual_review", "limit": "20"})
    assert home.status_code == 200
    assert str(home.url).endswith("/?limit=20&status=article_failed")
    assert "vid-a-000" in home.text
    assert "vid-a-001" not in home.text
    assert "vid-b-000" not in home.text
    assert re.search(r'<option value="article_failed"\s+selected', home.text)

    # Legacy mapping is a one-time page redirect; HTMX fragments use only ?status=.
    fragment = client.get(
        "/views/video-list",
        params={"status": "article_pending", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert fragment.status_code == 200
    assert "vid-a-001" in fragment.text
    assert "vid-a-000" not in fragment.text
    push_url = fragment.headers["HX-Push-Url"]
    assert "status=article_pending" in push_url
    assert "pipeline_status" not in push_url
    assert 'name="status" value="article_pending"' in fragment.text


def test_video_list_status_filter_lists_all_statuses(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    options = re.findall(r'<option value="([a-z_]+)"', response.text)
    for status in (
        "paused",
        "transcript_pending",
        "transcript_failed",
        "no_subtitle",
        "transcript_ready",
        "article_pending",
        "article_failed",
        "article_ready",
    ):
        assert status in options
    assert re.search(
        r'<option value="article_failed"\s*>\s*기사 생성 실패\s*</option>', response.text
    )


def test_video_list_article_retry_and_eligibility_follow_activity(client: TestClient) -> None:
    """기사 실패·재시도 대기 행만 재시도 버튼과 기사화 선택을 허용하고, 실행 중 행은 막는다."""
    _seed_channels_and_videos()
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        _set_status(conn, "vid-a-000", "article_failed")
        _set_status(conn, "vid-a-001", "article_retry_wait", attempt_count=1, error_message="x")
        _set_status(conn, "vid-a-002", "article_running")
        _set_status(conn, "vid-b-000", "article_pending")
        conn.commit()

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    html = response.text

    def _eligible(video_id: str) -> set[str]:
        return set(
            re.findall(
                rf'value="{video_id}"\s+data-video-select-item\s+data-article-eligible="(\d)"',
                html,
            )
        )

    for video_id in ("vid-a-000", "vid-a-001"):
        # 모바일 카드와 데스크톱 행 양쪽에 재시도 버튼을 노출한다.
        assert html.count(f'hx-post="/api/videos/{video_id}/retry"') == 2
        assert _eligible(video_id) == {"1"}
    for video_id in ("vid-a-002", "vid-b-000"):
        assert f'hx-post="/api/videos/{video_id}/retry"' not in html
        assert _eligible(video_id) == {"0"}
    # transcript_pending 행도 기사화 대상이 아니다.
    assert _eligible("vid-b-001") == {"0"}

    assert 'data-pipeline-status="article_pending" data-pipeline-activity="retry_wait"' in html
    assert 'data-pipeline-status="article_pending" data-pipeline-activity="running"' in html
    assert 'data-pipeline-status="article_failed"' in html


def test_video_list_badge_marks_regenerating_article(client: TestClient) -> None:
    _seed_channels_and_videos()
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        _set_status(conn, "vid-a-000", "article_ready")
        seed_video_job_sync(conn, "vid-a-000", "article", status="running", origin="manual")
        conn.commit()

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    assert response.text.count("data-pipeline-regenerating") == 2


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


def test_video_list_renders_status_badge_from_pipeline_status(client: TestClient) -> None:
    _seed_channels_and_videos()

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    assert "status-badge--transcript-pending" in response.text


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


def test_video_download_selected_preserves_status_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _set_status(conn, "vid-b-001", "no_subtitle")
        conn.commit()

    response = client.post(
        "/views/videos/download-selected",
        data={"_page": "1", "_limit": "20", "_status": "no_subtitle"},
    )

    assert response.status_code == 200
    assert 'name="_status" value="no_subtitle"' in response.text
    assert "vid-b-001" in response.text
    assert "vid-a-000" not in response.text


def test_video_download_selected_duplicate_only_returns_info_tone(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.domains.downloads.service.is_ffmpeg_available", lambda: True)
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
    monkeypatch.setattr("app.domains.downloads.service.is_ffmpeg_available", lambda: True)

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
    assert "vid-a-001" in html
    assert "data-video-list-cards" in html
    assert "video-list-table" in html
    assert "xl:min-w-[960px]" in html or "min-w-0" in html
    assert "lg:hidden" in html
    assert "hidden overflow-x-auto lg:block" in html
    assert 'name="viewed"' in html
    assert "data-video-bulk-actions" in html
    assert "data-video-filters" in html
    assert "video-list-filters" in html

    empty_response = client.get(
        "/views/video-list",
        params={"channel_id": "UC_NO_MATCH", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert empty_response.status_code == 200
    assert "영상이 없습니다" in empty_response.text or "No videos yet" in empty_response.text


def test_video_list_marks_article_eligible_for_transcript_ready(client: TestClient) -> None:
    """자막 완료·기사 없음 행은 기사화 가능 표시와 인라인 기사화 CTA를 노출한다."""
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _set_status(conn, "vid-a-001", "transcript_ready")
        conn.commit()
    _seed_video_outputs("vid-a-001", transcript=True, article=False)

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    html = response.text
    assert 'data-article-eligible="1"' in html
    assert "data-video-list-inline-article" in html


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


def test_video_article_selected_preserves_status_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        _set_status(conn, "vid-a-002", "article_ready")
        conn.commit()

    response = client.post(
        "/views/videos/article-request-selected",
        data={"_page": "1", "_limit": "20", "_status": "article_ready"},
    )

    assert response.status_code == 200
    assert 'name="_status" value="article_ready"' in response.text
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
            _insert_video(
                conn,
                f"vid-manual-limit-{idx:02d}",
                "UC_MANUAL_LIMIT",
                f"Manual Limit {idx}",
                f"2026-02-{10 + idx:02d}T00:00:00+00:00",
                "article_ready",
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


def test_video_list_viewed_filter(client: TestClient) -> None:
    _seed_channels_and_videos()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE videos SET viewed_at = datetime('now') WHERE video_id = 'vid-a-000'")
        conn.commit()

    unread = client.get(
        "/views/video-list",
        params={"viewed": "unread", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert unread.status_code == 200
    assert "vid-a-000" not in unread.text
    assert "vid-a-001" in unread.text
    assert 'name="viewed"' in unread.text

    read = client.get(
        "/views/video-list",
        params={"viewed": "read", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert read.status_code == 200
    assert "vid-a-000" in read.text
    assert "vid-a-001" not in read.text


def test_video_list_sets_home_push_url_header_with_viewed(client: TestClient) -> None:
    _seed_channels_and_videos()
    response = client.get(
        "/views/video-list",
        params={"viewed": "unread", "page": "1", "limit": "20"},
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    push_url = response.headers.get("HX-Push-Url") or ""
    assert "viewed=unread" in push_url


def test_video_article_preview_modal_marks_viewed(client: TestClient) -> None:
    _seed_channels_and_videos()
    _seed_video_outputs("vid-a-000", transcript=True, article=True)
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT viewed_at FROM videos WHERE video_id = ?",
            ("vid-a-000",),
        ).fetchone()
        assert before is not None
        assert before[0] is None

    response = client.get(
        "/views/videos/vid-a-000/article-preview-modal",
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    assert "data-article-preview-modal" in response.text

    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            "SELECT viewed_at FROM videos WHERE video_id = ?",
            ("vid-a-000",),
        ).fetchone()
        assert after is not None
        assert after[0] is not None


def test_video_list_shows_article_title_and_lead_summary(client: TestClient) -> None:
    _seed_channels_and_videos()
    _seed_video_outputs("vid-a-000", transcript=True, article=True)

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)

    assert response.status_code == 200
    html = response.text
    # mobile card + desktop row
    assert html.count("data-video-article-summary") == 2
    assert html.count("Article for vid-a-000") >= 2
    assert "Lead paragraph" in html


def _set_video_statuses(statuses: dict[str, str]) -> None:
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        for video_id, pipeline_status in statuses.items():
            _set_status(conn, video_id, pipeline_status, attempt_count=3, error_message="boom")
        conn.commit()


def _video_transcript_state(video_id: str) -> tuple[str, int, str | None]:
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        row = conn.execute(
            "SELECT pipeline_status, transcript_attempt_count, transcript_error_message "
            "FROM video_pipeline_v WHERE video_id = ?",
            (video_id,),
        ).fetchone()
    return str(row[0]), int(row[1]), row[2]


def test_video_list_renders_transcript_retry_only_for_missing_transcript_rows(
    client: TestClient,
) -> None:
    """자막 없음·자막 실패 행만 복사 칸에 재수집 버튼을 모바일·데스크톱 양쪽에 노출한다."""
    _seed_channels_and_videos()
    _set_video_statuses(
        {
            "vid-a-000": "no_subtitle",
            "vid-a-001": "transcript_failed",
            "vid-a-002": "transcript_ready",
            "vid-b-000": "no_subtitle",
        }
    )
    _seed_video_outputs("vid-a-002", transcript=True, article=False)
    # 자막이 저장된 no_subtitle 행은 복사 버튼이 우선한다.
    _seed_video_outputs("vid-b-000", transcript=True, article=False)

    response = client.get("/views/video-list", params={"limit": "20"}, headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    html = response.text

    for video_id in ("vid-a-000", "vid-a-001"):
        assert html.count(f'hx-post="/views/videos/{video_id}/transcript-retry"') == 2
    for video_id in ("vid-a-002", "vid-b-000", "vid-b-001"):
        assert f'hx-post="/views/videos/{video_id}/transcript-retry"' not in html
    assert html.count("data-video-list-transcript-retry") == 4
    assert html.count('data-transcript-url="/api/videos/vid-b-000/transcript"') == 2
    assert 'aria-label="자막 재수집"' in html or 'aria-label="Retry transcript"' in html


def test_video_list_transcript_retry_requeues_and_rerenders_list(client: TestClient) -> None:
    _seed_channels_and_videos()
    _set_video_statuses({"vid-a-000": "no_subtitle", "vid-a-001": "transcript_failed"})

    for video_id in ("vid-a-000", "vid-a-001"):
        response = client.post(
            f"/views/videos/{video_id}/transcript-retry",
            data={"_page": "1", "_limit": "20", "_sort": "upload_time", "_order": "desc"},
            headers=FRAGMENT_HEADERS,
        )
        assert response.status_code == 200
        assert "data-video-list-cards" in response.text
        assert f'hx-post="/views/videos/{video_id}/transcript-retry"' not in response.text
        toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
        assert toast["tone"] == "success"
        assert _video_transcript_state(video_id) == ("transcript_pending", 0, None)


def test_video_list_transcript_retry_skips_ineligible_status(client: TestClient) -> None:
    _seed_channels_and_videos()
    _set_video_statuses({"vid-a-000": "transcript_ready"})

    for video_id, expected_status in (
        ("vid-a-000", "transcript_ready"),
        ("vid-a-001", "transcript_pending"),
    ):
        response = client.post(
            f"/views/videos/{video_id}/transcript-retry",
            data={"_page": "1", "_limit": "20"},
            headers=FRAGMENT_HEADERS,
        )
        assert response.status_code == 200
        toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
        assert toast["tone"] == "info"
        assert _video_transcript_state(video_id)[0] == expected_status

    missing = client.post(
        "/views/videos/vid-missing/transcript-retry",
        data={"_page": "1"},
        headers=FRAGMENT_HEADERS,
    )
    assert missing.status_code == 200
    toast = json.loads(missing.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast["tone"] == "info"
    assert "worker" not in toast["message"]


def test_video_list_transcript_retry_reports_disabled_worker(client: TestClient) -> None:
    _seed_channels_and_videos()
    _set_video_statuses({"vid-a-000": "no_subtitle"})
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) "
            "VALUES ('worker_transcript_enabled', 'false')"
        )
        conn.commit()

    response = client.post(
        "/views/videos/vid-a-000/transcript-retry",
        data={"_page": "1"},
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
    assert toast["tone"] == "info"
    assert "worker" in toast["message"]
    assert _video_transcript_state("vid-a-000")[0] == "transcript_pending"


def test_video_list_transcript_retry_preserves_filters(client: TestClient) -> None:
    _seed_channels_and_videos()
    _set_video_statuses({"vid-a-000": "no_subtitle", "vid-b-000": "no_subtitle"})

    response = client.post(
        "/views/videos/vid-a-000/transcript-retry",
        data={"_page": "1", "_limit": "20", "_status": "no_subtitle"},
        headers=FRAGMENT_HEADERS,
    )
    assert response.status_code == 200
    html = response.text
    # 재대기열에 들어간 vid-a-000은 no_subtitle 필터에서 빠지고 vid-b-000만 남는다.
    assert 'hx-post="/views/videos/vid-b-000/transcript-retry"' in html
    assert "Video A-0" not in html


def test_video_list_transcript_retry_skips_active_manual_jobs_and_stored_transcript(
    client: TestClient,
) -> None:
    """수동 기사화·자막 job이 진행 중이거나 자막이 이미 저장된 영상은 재대기열에 넣지 않는다."""
    _seed_channels_and_videos()
    _set_video_statuses(
        {
            "vid-a-000": "no_subtitle",
            "vid-a-001": "no_subtitle",
            "vid-a-002": "transcript_failed",
            "vid-b-000": "no_subtitle",
        }
    )
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        # Active manual article request without a transcript: an active article job.
        seed_video_job_sync(conn, "vid-a-000", "article", origin="manual")
        # Running manual transcript request: the current transcript job is running.
        seed_video_job_sync(conn, "vid-a-001", "transcript", status="running", origin="manual")
        # A finished (failed) manual article request does not block the retry.
        seed_video_job_sync(conn, "vid-b-000", "article", status="failed", origin="manual")
        conn.commit()
    _seed_video_outputs("vid-a-002", transcript=True, article=False)

    for video_id, requeued in (
        ("vid-a-000", False),
        ("vid-a-001", False),
        ("vid-a-002", False),
        ("vid-b-000", True),
    ):
        before_status = _video_transcript_state(video_id)[0]
        response = client.post(
            f"/views/videos/{video_id}/transcript-retry",
            data={"_page": "1"},
            headers=FRAGMENT_HEADERS,
        )
        assert response.status_code == 200
        toast = json.loads(response.headers["HX-Trigger"])["video-transcript-request-toast"]
        assert toast["tone"] == ("success" if requeued else "info")
        after_status = _video_transcript_state(video_id)[0]
        if requeued:
            assert after_status == "transcript_pending"
        else:
            assert after_status == before_status
