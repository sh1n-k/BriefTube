from __future__ import annotations

import json
import os
import sqlite3

from fastapi.testclient import TestClient

from app.repositories import videos as videos_repo
from tests.helpers.app_repo import call_repo
from tests.helpers.db_seed import seed_video_job_sync

FRAGMENT_HEADERS = {"HX-Request": "true"}

# Seed key → video_jobs rows that reproduce it in video_pipeline_v.
# "<status>" or "<status>:<activity>" (the activity defaults to queued for pending statuses).
_STATUS_JOBS: dict[str, list[dict[str, object]]] = {
    "transcript_pending": [{"kind": "transcript"}],
    "transcript_pending:running": [{"kind": "transcript", "status": "running"}],
    "transcript_failed": [
        {
            "kind": "transcript",
            "status": "failed",
            "error_code": "non_retryable_failure",
            "error_message": "video unavailable",
        }
    ],
    "no_subtitle": [{"kind": "transcript", "status": "failed", "error_code": "no_subtitle"}],
    "article_pending": [{"kind": "article"}],
    "article_pending:running": [{"kind": "article", "status": "running"}],
    "article_pending:retry_wait": [{"kind": "article", "error_code": "llm_provider_timeout"}],
    "article_failed": [{"kind": "article", "status": "failed", "error_code": "retry_exhausted"}],
    "article_ready:regenerating": [{"kind": "article", "origin": "manual"}],
}


def _seed_status_jobs(conn: sqlite3.Connection, video_id: str, pipeline_status: str) -> None:
    for job in _STATUS_JOBS.get(pipeline_status, []):
        params = dict(job)
        kind = str(params.pop("kind"))
        seed_video_job_sync(conn, video_id, kind, **params)  # type: ignore[arg-type]


def _seed_video(
    *,
    video_id: str = "vid-001",
    channel_id: str = "UC_AAA",
    channel_name: str = "Channel A",
    title: str = "Test Video",
    pipeline_status: str = "article_ready",
    raw_text: str | None = "Hello world transcript",
    language: str | None = "ko",
    source_type: str | None = "auto",
    article_title: str | None = "Test Article",
    lead: str | None = "Lead paragraph",
    body: str | None = "Article body text",
    fact_box: str | None = '{"key": "value"}',
    timestamps: str | None = '[{"t": 0, "text": "start"}]',
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_reasoning_effort: str | None = None,
    llm_generated_at: str | None = None,
) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                channel_id,
                channel_name,
                f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time)
            VALUES (?, ?, ?, ?)
            """,
            (video_id, channel_id, title, "2026-02-10T00:00:00+00:00"),
        )
        _seed_status_jobs(conn, video_id, pipeline_status)
        if raw_text is not None:
            conn.execute(
                "INSERT INTO transcripts(video_id, raw_text, language, source_type) VALUES (?, ?, ?, ?)",
                (video_id, raw_text, language, source_type),
            )
        if article_title is not None:
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
                    article_title,
                    lead,
                    body,
                    fact_box,
                    timestamps,
                    llm_provider or "unknown",
                    llm_model or "",
                    llm_reasoning_effort or "",
                    llm_generated_at or "2026-02-10T00:00:00+00:00",
                ),
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


def test_detail_page_missing_video_returns_404_with_not_found_ui(client: TestClient) -> None:
    response = client.get("/videos/missing-video-id")

    assert response.status_code == 404
    assert "영상을 찾을 수 없습니다." in response.text


def test_detail_youtube_embed_card_order(client: TestClient) -> None:
    """메타데이터 카드와 dynamic bundle 사이에 플레이어 카드가 배치된다."""
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    meta_idx = html.index("data-detail-meta-card")
    player_idx = html.index("data-detail-player-card")
    dynamic_idx = html.index("data-video-detail-dynamic-fragment")
    assert meta_idx < player_idx < dynamic_idx


def test_detail_youtube_embed_contract(client: TestClient) -> None:
    """임베드 플레이어 마크업/외부 UI 스크립트 계약이 포함된다."""
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert "data-youtube-embed" in html
    assert 'data-youtube-video-id="vid-001"' in html
    assert "data-youtube-loading" in html
    assert "data-youtube-player-slot" in html
    assert "data-youtube-fallback-blocked" in html
    assert "data-youtube-fallback-error" in html
    assert "/static/js/main-ui.js" in html


def test_detail_empty_fact_box_hidden(client: TestClient) -> None:
    """fact_box='{}'이면 핵심 팩트 미렌더링"""
    _seed_video(fact_box="{}")
    response = client.get("/videos/vid-001")
    html = response.text
    assert "핵심 팩트" not in html
    assert "Key Facts" not in html


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


def test_detail_article_card_starts_open_with_open_flag(client: TestClient) -> None:
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    card_idx = html.index("data-detail-article-card")
    segment = html[max(0, card_idx - 120) : card_idx + 80]
    assert "data-collapsible-open" in segment
    assert "data-detail-fact-box" in html or "fact-box-panel" in html


def test_detail_article_ready_badge_visible_when_article_exists(client: TestClient) -> None:
    _seed_video()
    response = client.get("/videos/vid-001")
    html = response.text
    assert "완성" in html or "Ready" in html


def test_detail_article_modal_contract_and_raw_body_preview(client: TestClient) -> None:
    _seed_video(
        lead="Lead with **strong** text.",
        body="# Heading\n\nThis is **markdown** body.",
        fact_box="- **fact** item",
    )
    response = client.get("/videos/vid-001")
    html = response.text
    assert '<div class="article-rendered">' in html
    assert "# Heading" in html
    assert "data-article-preview-open" in html
    assert "data-article-preview-modal" in html
    assert "data-article-preview-content" in html
    assert 'data-copy-target="article-copy-source"' in html
    assert 'class="article-rendered"' in html
    assert "<h1>Heading</h1>" in html
    assert "<strong>markdown</strong>" in html
    assert "<strong>strong</strong>" in html
    assert "<strong>fact</strong>" in html
    assert (
        "문서 보기 버튼으로 렌더링된 기사를 확인할 수 있습니다." in html
        or "Use the view button to read the rendered article." in html
    )


def test_detail_fact_box_json_renders_as_structured_content(client: TestClient) -> None:
    _seed_video(
        fact_box=json.dumps(
            {
                "source_title": "Embracing Failing",
                "central_claim": "실패를 예상하고 반복 학습하는 태도가 중요하다.",
                "key_lessons": [
                    "에러는 읽고 이해해야 한다.",
                    "직접 부딪히는 시간이 장기적으로 차이를 만든다.",
                ],
            },
            ensure_ascii=False,
        )
    )

    response = client.get("/videos/vid-001")

    assert response.status_code == 200
    html = response.text
    assert '{"source_title"' not in html
    assert "원본 제목" in html
    assert "핵심 주장" in html
    assert "핵심 교훈" in html
    assert "Embracing Failing" in html
    assert "실패를 예상하고 반복 학습하는 태도가 중요하다." in html
    assert "에러는 읽고 이해해야 한다." in html
    assert 'class="fact-box-grid"' in html


def test_detail_fragment_fact_box_json_renders_as_structured_content(client: TestClient) -> None:
    _seed_video(
        fact_box='{"named_examples":["A","B"],"explicit_uncertainties":"기억이 불완전하다."}'
    )

    response = client.get("/views/videos/vid-001/dynamic-fragment", headers=FRAGMENT_HEADERS)

    assert response.status_code == 200
    html = response.text
    assert '{"named_examples"' not in html
    assert "대표 사례" in html
    assert "불확실한 점" in html
    assert ">A<" in html
    assert ">B<" in html
    assert "기억이 불완전하다." in html


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


def test_detail_page_disables_auto_refresh_when_article_is_ready(client: TestClient) -> None:
    _seed_video()

    response = client.get("/videos/vid-001")

    assert response.status_code == 200
    assert 'data-video-detail-refresh-url="/views/videos/vid-001/dynamic-fragment"' in response.text
    assert 'data-video-detail-auto-refresh="0"' in response.text


def test_detail_fragment_enables_auto_refresh_for_incomplete_article(client: TestClient) -> None:
    _seed_video(
        video_id="vid-pending-001",
        pipeline_status="article_pending",
        article_title=None,
        raw_text="Pending transcript",
    )

    response = client.get(
        "/views/videos/vid-pending-001/dynamic-fragment",
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    html = response.text
    assert "<html" not in html.lower()
    assert 'data-video-detail-refresh-url="/views/videos/vid-pending-001/dynamic-fragment"' in html
    assert 'data-video-detail-auto-refresh="1"' in html


def test_detail_fragment_disables_auto_refresh_for_terminal_failed_status(
    client: TestClient,
) -> None:
    _seed_video(
        video_id="vid-failed-001",
        pipeline_status="no_subtitle",
        article_title=None,
        raw_text=None,
        language=None,
        source_type=None,
    )

    response = client.get(
        "/views/videos/vid-failed-001/dynamic-fragment",
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    assert 'data-video-detail-auto-refresh="0"' in response.text


def test_detail_page_shows_llm_article_metadata(client: TestClient) -> None:
    _seed_video(
        llm_provider="codex",
        llm_model="gpt-5.3-codex",
        llm_reasoning_effort="low",
        llm_generated_at="2026-03-02T09:30:00+00:00",
    )
    response = client.get("/videos/vid-001")
    html = response.text
    assert "LLM 생성 정보" in html or "LLM Metadata" in html
    assert "codex" in html
    assert "gpt-5.3-codex" in html
    assert "low" in html


def test_detail_article_request_requeues_ready_video_as_regeneration(client: TestClient) -> None:
    _seed_video(video_id="vid-requeue-001", pipeline_status="article_ready")
    response = client.post("/views/videos/vid-requeue-001/article-request")
    assert response.status_code == 200

    trigger_header = response.headers.get("HX-Trigger", "")
    payload = json.loads(trigger_header)
    toast = payload.get("video-article-request-toast", {})
    message = str(toast.get("message") or "")
    assert "재시도 1건" in message or "retry 1" in message

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT pipeline_status, article_attempt_count, pipeline_activity, activity_kind,
                   article_job_origin
            FROM video_pipeline_v
            WHERE video_id = ?
            """,
            ("vid-requeue-001",),
        ).fetchone()
        assert row is not None
        # The article is kept while it regenerates, so the status stays article_ready.
        assert row[0] == "article_ready"
        assert row[1] == 0
        assert row[2] == "queued"
        assert row[3] == "article"
        assert row[4] == "manual"
        article_row = conn.execute(
            "SELECT COUNT(1) FROM articles WHERE video_id = ?",
            ("vid-requeue-001",),
        ).fetchone()
        assert article_row is not None
        assert int(article_row[0] or 0) == 1


def test_video_repository_includes_llm_article_metadata(client: TestClient) -> None:
    _seed_video(
        llm_provider="claude",
        llm_model="sonnet",
        llm_reasoning_effort="high",
        llm_generated_at="2026-03-02T11:00:00+00:00",
    )

    detail_payload = call_repo(client, videos_repo.get_video_detail, "vid-001")
    assert detail_payload["llm_provider"] == "claude"
    assert detail_payload["llm_model"] == "sonnet"
    assert detail_payload["llm_reasoning_effort"] == "high"
    assert detail_payload["llm_generated_at"] == "2026-03-02T11:00:00+00:00"


_TOC_BODY = (
    "## 첫 섹션\n\n첫 문단.\n\n## 두 번째 & 섹션\n\n둘째 문단.\n\n## 세 번째 섹션\n\n셋째 문단."
)


def test_detail_article_reading_order_lead_body_facts_meta(client: TestClient) -> None:
    """리드 → 본문 → 핵심 팩트 → LLM 메타 순서이며 리드는 이탤릭이 아니다."""
    _seed_video(lead="Lead marker", body="Body marker paragraph", fact_box="- Fact marker")
    html = client.get("/videos/vid-001").text
    card = html[html.index("data-detail-article-card") :]
    card = card[: card.index("data-article-preview-modal")]
    lead_idx = card.index("Lead marker")
    fact_idx = card.index("Fact marker")
    body_idx = card.index("Body marker paragraph")
    meta_idx = card.index("data-detail-article-meta")
    assert lead_idx < body_idx < fact_idx < meta_idx
    lead_tag = card[card.index("data-article-lead") - 200 : card.index("data-article-lead")]
    assert "italic" not in lead_tag
    assert "<details" in card[meta_idx - 200 : meta_idx]


def test_detail_article_toc_rendered_for_three_sections(client: TestClient) -> None:
    _seed_video(body=_TOC_BODY)
    html = client.get("/videos/vid-001").text
    assert "data-article-toc-root" in html
    assert html.count('data-article-toc-index="0"') == 2  # detail card + preview modal
    assert 'data-article-toc-index="2"' in html
    assert "두 번째 &amp; 섹션" in html


def test_detail_article_toc_hidden_for_short_articles(client: TestClient) -> None:
    _seed_video(body="## 하나\n\n본문.\n\n## 둘\n\n본문.")
    html = client.get("/videos/vid-001").text
    assert "data-article-toc-index" not in html


def test_detail_timestamps_render_youtube_links(client: TestClient) -> None:
    _seed_video(timestamps="- [01:05] 도입\n- [1:02:03] 결론")
    html = client.get("/videos/vid-001").text
    assert "data-detail-timestamps" in html
    assert "https://www.youtube.com/watch?v=vid-001&amp;t=65s" in html
    assert 'data-article-timestamp-seconds="3723"' in html
    assert 'data-article-timestamp-video-id="vid-001"' in html
    assert "1:02:03" in html


def test_detail_timestamp_placeholder_text_hidden(client: TestClient) -> None:
    """근거 없는 안내 문구만 있는 기존 기사는 타임스탬프 블록을 숨긴다."""
    _seed_video(timestamps="원문에 타임스탬프 없음")
    html = client.get("/videos/vid-001").text
    assert "data-detail-timestamps" not in html
    assert "원문에 타임스탬프 없음" not in html


def test_detail_legacy_guessed_timestamps_hidden(client: TestClient) -> None:
    """정규 형식이 아닌 기존(추정) 타임스탬프는 링크로 노출하지 않는다."""
    _seed_video(timestamps="- 00:00 도입\n- 01:05 추정 구간")
    html = client.get("/videos/vid-001").text
    assert "data-detail-timestamps" not in html
    assert "data-article-timestamp-seconds" not in html


def test_detail_shows_article_and_regenerating_badge_while_regenerating(
    client: TestClient,
) -> None:
    _seed_video(video_id="vid-regen-001", pipeline_status="article_ready:regenerating")

    response = client.get("/videos/vid-regen-001")

    assert response.status_code == 200
    html = response.text
    assert "Test Article" in html
    assert 'data-pipeline-status="article_ready"' in html
    assert 'data-pipeline-activity="queued"' in html
    assert "data-pipeline-regenerating" in html
    assert 'data-video-detail-auto-refresh="1"' in html


def test_detail_ready_article_without_job_has_no_regenerating_badge(client: TestClient) -> None:
    _seed_video(video_id="vid-regen-002")

    html = client.get("/videos/vid-regen-002").text

    assert 'data-pipeline-status="article_ready"' in html
    assert 'data-pipeline-activity="idle"' in html
    assert "data-pipeline-regenerating" not in html


def test_detail_article_request_skips_ready_video_already_regenerating(
    client: TestClient,
) -> None:
    _seed_video(video_id="vid-regen-003", pipeline_status="article_ready:regenerating")

    response = client.post("/views/videos/vid-regen-003/article-request")

    assert response.status_code == 200
    toast = json.loads(response.headers["HX-Trigger"])["video-article-request-toast"]
    message = str(toast.get("message") or "")
    assert "신규 0건, 재시도 0건, 건너뜀 1건, 실패 0건" in message
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        jobs = conn.execute(
            "SELECT kind, status, attempt_count FROM video_jobs WHERE video_id = ?",
            ("vid-regen-003",),
        ).fetchall()
    assert jobs == [("article", "queued", 0)]


def test_detail_shows_transcript_failure_reason(client: TestClient) -> None:
    _seed_video(
        video_id="vid-err-tx",
        pipeline_status="transcript_failed",
        raw_text=None,
        article_title=None,
    )

    html = client.get("/videos/vid-err-tx").text

    assert 'data-pipeline-status="transcript_failed"' in html
    assert 'data-pipeline-error="transcript"' in html
    assert "실패 원인: video unavailable" in html
    assert 'data-pipeline-error="article"' not in html


def test_detail_transcript_failure_reason_falls_back_to_error_code(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_video(
        video_id="vid-err-tx-code", pipeline_status="paused", raw_text=None, article_title=None
    )
    with sqlite3.connect(db_path) as conn:
        seed_video_job_sync(
            conn,
            "vid-err-tx-code",
            "transcript",
            status="failed",
            error_code="non_retryable_failure",
        )
        conn.commit()

    html = client.get("/videos/vid-err-tx-code").text

    assert 'data-pipeline-error="transcript"' in html
    assert "실패 원인: non_retryable_failure" in html


def test_detail_shows_article_failure_reason(client: TestClient) -> None:
    _seed_video(video_id="vid-err-article", pipeline_status="article_failed", article_title=None)

    html = client.get("/videos/vid-err-article").text

    assert 'data-pipeline-status="article_failed"' in html
    assert 'data-pipeline-error="article"' in html
    assert "retry_exhausted" in html
    assert 'data-pipeline-error="transcript"' not in html


def test_detail_shows_article_retry_wait_reason(client: TestClient) -> None:
    _seed_video(
        video_id="vid-err-retry",
        pipeline_status="article_pending:retry_wait",
        article_title=None,
    )

    html = client.get("/videos/vid-err-retry").text

    assert 'data-pipeline-status="article_pending"' in html
    assert 'data-pipeline-activity="retry_wait"' in html
    assert 'data-pipeline-error="article"' in html
    assert "llm_provider_timeout" in html
    assert 'data-video-detail-auto-refresh="1"' in html


def test_detail_hides_failure_reason_for_healthy_states(client: TestClient) -> None:
    _seed_video(video_id="vid-ok-ready")
    _seed_video(video_id="vid-ok-pending", pipeline_status="article_pending", article_title=None)

    for video_id in ("vid-ok-ready", "vid-ok-pending"):
        html = client.get(f"/videos/{video_id}").text
        assert "data-pipeline-error" not in html
