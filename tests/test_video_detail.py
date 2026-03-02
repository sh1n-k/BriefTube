from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_video(
    *,
    video_id: str = "vid-001",
    channel_id: str = "UC_AAA",
    channel_name: str = "Channel A",
    title: str = "Test Video",
    pipeline_status: str = "done",
    raw_text: str | None = "Hello world transcript",
    language: str | None = "ko",
    source_type: str | None = "auto",
    article_title: str | None = "Test Article",
    lead: str | None = "Lead paragraph",
    body: str | None = "Article body text",
    fact_box: str | None = '{"key": "value"}',
    timestamps: str | None = '[{"t": 0, "text": "start"}]',
) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (channel_id, channel_name, f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"),
        )
        conn.execute(
            """
            INSERT INTO videos(
                video_id, channel_id, title, upload_time,
                pipeline_status, retry_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (video_id, channel_id, title, "2026-02-10T00:00:00+00:00", pipeline_status, 0),
        )
        if raw_text is not None:
            conn.execute(
                "INSERT INTO transcripts(video_id, raw_text, language, source_type) VALUES (?, ?, ?, ?)",
                (video_id, raw_text, language, source_type),
            )
        if article_title is not None:
            conn.execute(
                "INSERT INTO articles(video_id, title, lead, body, fact_box, timestamps) VALUES (?, ?, ?, ?, ?, ?)",
                (video_id, article_title, lead, body, fact_box, timestamps),
            )
        conn.commit()


def test_detail_page_renders(client: TestClient) -> None:
    """상세 페이지 렌더링 + 채널명 표시"""
    _seed_video()
    response = client.get("/videos/vid-001")
    assert response.status_code == 200
    html = response.text
    assert "Channel A" in html
    assert "Test Video" in html
    assert "Test Article" in html
    assert "문서 보기" in html or "View document" in html


def test_detail_youtube_link(client: TestClient) -> None:
    """YouTube 외부 링크 존재"""
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert "https://www.youtube.com/watch?v=vid-001" in html


def test_detail_youtube_embed_card_order(client: TestClient) -> None:
    """메타데이터 카드와 기사 카드 사이에 플레이어 카드가 배치된다."""
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    meta_idx = html.index("data-detail-meta-card")
    player_idx = html.index("data-detail-player-card")
    article_idx = html.index("data-detail-article-card")
    assert meta_idx < player_idx < article_idx


def test_detail_youtube_embed_contract(client: TestClient) -> None:
    """임베드 플레이어 마크업/외부 UI 스크립트 계약이 포함된다."""
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert 'data-youtube-embed' in html
    assert 'data-youtube-video-id="vid-001"' in html
    assert "data-youtube-loading" in html
    assert "data-youtube-player-slot" in html
    assert "data-youtube-fallback-blocked" in html
    assert "data-youtube-fallback-error" in html
    assert '/static/js/main-ui.js' in html


def test_detail_empty_fact_box_hidden(client: TestClient) -> None:
    """fact_box='{}'이면 핵심 팩트 미렌더링"""
    _seed_video(fact_box="{}")
    response = client.get("/videos/vid-001")
    html = response.text
    assert "핵심 팩트" not in html
    assert "Key Facts" not in html


def test_detail_empty_timestamps_hidden(client: TestClient) -> None:
    """timestamps='[]'이면 타임스탬프 미렌더링"""
    _seed_video(timestamps="[]")
    response = client.get("/videos/vid-001")
    html = response.text
    assert "detail_timestamps" not in html or html.count("detail_timestamps") == 0


def test_detail_transcript_badges(client: TestClient) -> None:
    """자막 language/source_type 뱃지 표시"""
    _seed_video(language="ko", source_type="auto")
    response = client.get("/videos/vid-001")
    html = response.text
    assert 'data-transcript-badge="language"' in html
    assert 'data-transcript-badge="source"' in html
    assert "ko" in html
    assert "auto" in html


def test_detail_collapsible_attrs(client: TestClient) -> None:
    """collapsible data 속성 존재"""
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert "data-collapsible" in html
    assert "data-collapsible-toggle" in html
    assert "data-collapsible-body" in html
    assert "data-collapsible-icon" in html


def test_detail_article_card_starts_collapsed_without_open_flag(client: TestClient) -> None:
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    card_idx = html.index("data-detail-article-card")
    segment = html[card_idx: card_idx + 220]
    assert "data-collapsible-open" not in segment


def test_detail_article_ready_badge_visible_when_article_exists(client: TestClient) -> None:
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert "완성" in html or "Ready" in html


def test_detail_article_modal_contract_and_no_inline_body(client: TestClient) -> None:
    _seed_video(body="# Heading\n\nThis is **markdown** body.")
    response = client.get("/videos/vid-001")
    html = response.text
    assert 'data-article-preview-open' in html
    assert 'data-article-preview-modal' in html
    assert 'data-article-preview-content' in html
    assert "<h1>Heading</h1>" in html
    assert "<strong>markdown</strong>" in html
    assert "prose prose-sm max-w-none text-slate-700 whitespace-pre-wrap" not in html


def test_detail_copy_button_attrs(client: TestClient) -> None:
    """copy 버튼 data 속성 존재"""
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert 'data-copy-target="article-copy-source"' in html
    assert 'data-copy-target="transcript-copy-source"' in html
    assert "data-copy-toast" in html


def test_detail_download_button_contract(client: TestClient) -> None:
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert "data-video-download-open" in html
    assert 'data-video-id="vid-001"' in html
    assert 'data-download-default-quality="' in html
    assert "다운로드" in html or "Download" in html


def test_detail_article_request_button_contract(client: TestClient) -> None:
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert "data-video-article-request-button" in html
    assert 'data-video-id="vid-001"' in html
    assert "/views/videos/vid-001/article-request" in html
    assert 'hx-swap="none"' in html
