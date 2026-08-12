from __future__ import annotations

import os
import re
import sqlite3

from fastapi.testclient import TestClient

FRAGMENT_HEADERS = {"HX-Request": "true"}


def _seed_search_video(
    *,
    video_id: str,
    title: str,
    transcript: str,
    article_title: str,
    article_lead: str,
    article_body: str,
) -> None:
    db_path = os.environ["DB_PATH"]
    channel_id = "UCsearchresults001"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                channel_id,
                "Search Results Channel",
                f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, '2026-02-10T00:00:00+00:00', 'done')
            """,
            (video_id, channel_id, title),
        )
        conn.execute(
            "INSERT INTO transcripts(video_id, raw_text, language, source_type) VALUES (?, ?, 'ko', 'manual')",
            (video_id, transcript),
        )
        conn.execute(
            """
            INSERT INTO articles(video_id, title, lead, body)
            VALUES (?, ?, ?, ?)
            """,
            (video_id, article_title, article_lead, article_body),
        )
        conn.commit()


def test_search_results_fragment_returns_matching_results(client: TestClient) -> None:
    _seed_search_video(
        video_id="vid-search-blockchain",
        title="블록체인 기술 심층 해설",
        transcript="블록체인은 분산 원장 기술로 투명성과 보안성을 제공합니다.",
        article_title="블록체인의 핵심 원리",
        article_lead="분산 원장 기술의 기본 개념",
        article_body="블록체인 기술은 거래 기록을 분산 저장합니다.",
    )

    response = client.get("/views/search-results?q=블록체인", headers=FRAGMENT_HEADERS)

    assert response.status_code == 200
    html = response.text
    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert "블록체인" in html
    assert "검색 결과" in html
    assert "/videos/vid-search-blockchain" in html


def test_search_results_snippet_uses_match_context_and_strips_markdown(
    client: TestClient,
) -> None:
    _seed_search_video(
        video_id="vid-search-snippet",
        title="에이전트 검색 스니펫",
        transcript="서두 문장입니다. " + ("패딩 " * 40) + "핵심 키워드 에이전트 워크플로 설명.",
        article_title="## 한 곳에서 보는 요약",
        article_lead="서두",
        article_body=(
            "## 한 곳에서 보는 요약\n\n"
            + ("앞부분 패딩 문장. " * 20)
            + "여기서 **에이전트** 패턴을 다룹니다."
        ),
    )

    response = client.get("/views/search-results?q=에이전트", headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    html = response.text
    assert "에이전트" in html
    assert "<mark>" in html
    snippets = re.findall(
        r'class="[^"]*search-snippet[^"]*"[^>]*>(.*?)</p>',
        html,
        flags=re.DOTALL,
    )
    assert snippets
    joined = " ".join(snippets)
    assert "에이전트" in joined
    assert "<mark>" in joined
    assert "##" not in joined
    assert "서두 문장입니다." not in joined


def test_search_results_fragment_renders_empty_state(client: TestClient) -> None:
    _seed_search_video(
        video_id="vid-search-invest",
        title="투자전략 가이드",
        transcript="분산투자와 장기적 관점이 중요합니다.",
        article_title="효과적인 투자전략",
        article_lead="장기 투자와 분산 투자",
        article_body="리스크 관리와 포트폴리오 다변화를 다룹니다.",
    )

    response = client.get(
        "/views/search-results?q=존재하지않는검색어xyz",
        headers=FRAGMENT_HEADERS,
    )

    assert response.status_code == 200
    assert "일치하는 결과가 없습니다." in response.text
