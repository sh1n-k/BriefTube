"""E2E tests for the search functionality (HTMX search-results fragment)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    make_past_time,
    seed_article,
    seed_channel,
    seed_transcript,
    seed_video,
)

CHANNEL_ID = "UC_search_test_ch"
CHANNEL_NAME = "SearchTestChannel"

# Each video has a unique keyword in title, transcript, and article body
# so that FTS5 MATCH reliably returns the expected results.
VIDEOS = [
    {
        "video_id": "vid_search_ai",
        "title": "인공지능의 미래 전망 분석",
        "transcript": "인공지능 기술은 빠르게 발전하고 있으며 다양한 산업에 적용되고 있습니다.",
        "article_title": "인공지능 기술의 미래",
        "article_lead": "AI 기술이 산업 전반에 미치는 영향을 분석합니다.",
        "article_body": "인공지능은 헬스케어 금융 제조업 등 다양한 분야에서 혁신을 이끌고 있습니다.",
        "days_ago": 5,
    },
    {
        "video_id": "vid_search_blockchain",
        "title": "블록체인 기술 심층 해설",
        "transcript": "블록체인은 분산 원장 기술로 투명성과 보안성을 제공합니다.",
        "article_title": "블록체인의 핵심 원리",
        "article_lead": "분산 원장 기술의 기본 개념을 설명합니다.",
        "article_body": "블록체인 기술은 탈중앙화된 네트워크에서 거래를 기록하는 혁신적 방법입니다.",
        "days_ago": 4,
    },
    {
        "video_id": "vid_search_invest",
        "title": "투자전략 가이드 2026",
        "transcript": "투자전략을 수립할 때는 분산투자와 장기적 관점이 중요합니다.",
        "article_title": "효과적인 투자전략",
        "article_lead": "장기 투자와 분산 투자의 중요성.",
        "article_body": "투자전략의 핵심은 리스크 관리와 포트폴리오 다변화에 있습니다.",
        "days_ago": 3,
    },
    {
        "video_id": "vid_search_quantum",
        "title": "양자컴퓨팅 입문 강좌",
        "transcript": "양자컴퓨팅은 큐비트를 활용하여 병렬 연산을 수행합니다.",
        "article_title": "양자컴퓨팅 기초",
        "article_lead": "양자역학 원리를 활용한 차세대 컴퓨팅.",
        "article_body": "양자컴퓨팅은 기존 컴퓨터로는 풀기 어려운 문제를 해결할 수 있습니다.",
        "days_ago": 2,
    },
    {
        "video_id": "vid_search_climate",
        "title": "기후변화 대응 리포트",
        "transcript": "기후변화는 전 세계적으로 심각한 환경 문제를 야기하고 있습니다.",
        "article_title": "기후변화와 탄소중립",
        "article_lead": "지구 온난화의 현황과 대응 방안.",
        "article_body": "기후변화에 대응하기 위해 각국은 탄소중립 정책을 추진하고 있습니다.",
        "days_ago": 1,
    },
]


def _seed_search_data(db_path: str) -> None:
    """Insert 1 channel + 5 done videos with transcripts and articles."""
    seed_channel(db_path, CHANNEL_ID, CHANNEL_NAME)
    for v in VIDEOS:
        seed_video(
            db_path,
            v["video_id"],
            CHANNEL_ID,
            v["title"],
            upload_time=make_past_time(v["days_ago"]),
            pipeline_status="done",
        )
        seed_transcript(db_path, v["video_id"], v["transcript"])
        seed_article(
            db_path,
            v["video_id"],
            v["article_title"],
            v["article_lead"],
            v["article_body"],
        )


@pytest.fixture(scope="module", autouse=True)
def _seed(e2e_server: dict) -> None:
    _seed_search_data(e2e_server["db_path"])


# ---------- helpers ----------

def _go_home(page: Page) -> None:
    page.goto(page._e2e_base_url)
    page.wait_for_load_state("networkidle")


def _submit_search(page: Page, keyword: str) -> None:
    """Fill the search input and submit the HTMX search form."""
    search_input = page.locator("form[hx-get='/views/search-results'] input[name='q']")
    search_input.fill(keyword)
    search_input.press("Enter")
    # Wait for HTMX to populate #search-results
    page.locator("#search-results").wait_for(state="attached")


# ---------- tests ----------

def test_search_returns_results(e2e_page: Page) -> None:
    """Keyword search populates the #search-results HTMX fragment with results."""
    page = e2e_page
    _go_home(page)
    _submit_search(page, "블록체인")

    results_container = page.locator("#search-results")
    # The fragment should contain a heading with the query
    expect(results_container.locator("h3")).to_contain_text('Results for "블록체인"')
    # At least one result item
    result_items = results_container.locator("ul > li")
    expect(result_items.first).to_be_visible()
    # The result should link to the matching video
    expect(results_container.locator("a[href='/videos/vid_search_blockchain']")).to_be_visible()


def test_search_highlights_matching_text(e2e_page: Page) -> None:
    """Search result displays a snippet from the matching document."""
    page = e2e_page
    _go_home(page)
    _submit_search(page, "투자전략")

    results_container = page.locator("#search-results")
    # Wait for results to appear
    snippet_locator = results_container.locator("ul > li p")
    expect(snippet_locator.first).to_be_visible()

    # Collect all snippet texts; at least one must contain the keyword
    # The snippet comes from substr(raw_text, 1, 240) or substr(body, 1, 240)
    # and the template appends "..."
    all_snippets = snippet_locator.all_text_contents()
    assert any("투자전략" in text for text in all_snippets), (
        f"Expected at least one snippet to contain '투자전략', got: {all_snippets}"
    )


def test_search_no_results(e2e_page: Page) -> None:
    """Non-existent keyword shows the 'No matches found.' empty state."""
    page = e2e_page
    _go_home(page)
    _submit_search(page, "존재하지않는검색어xyz")

    results_container = page.locator("#search-results")
    # The template renders "No matches found." inside <li> when results are empty
    empty_message = results_container.locator("li")
    expect(empty_message).to_contain_text("No matches found.")


def test_search_links_to_video_detail(e2e_page: Page) -> None:
    """Clicking a search result navigates to the video detail page."""
    page = e2e_page
    _go_home(page)
    _submit_search(page, "양자컴퓨팅")

    results_container = page.locator("#search-results")
    link = results_container.locator("a[href='/videos/vid_search_quantum']")
    expect(link).to_be_visible()

    link.click()
    page.wait_for_load_state("networkidle")

    # Verify we navigated to the video detail page
    expect(page).to_have_url(f"{page._e2e_base_url}/videos/vid_search_quantum")
    expect(page.locator("#video-detail-wrap")).to_be_visible()


def test_search_preserves_query_in_url(e2e_page: Page) -> None:
    """Search from home page and verify query works via the HTMX fragment endpoint."""
    page = e2e_page

    # Navigate to home page and perform a search
    page.goto(f"{page._e2e_base_url}/")
    page.wait_for_load_state("networkidle")

    search_input = page.locator("input[name='q']")
    search_input.fill("기후변화")
    search_input.press("Enter")

    # Wait for HTMX search results to load
    results = page.locator("#search-results")
    expect(results).to_be_attached(timeout=10_000)

    # The fragment endpoint should preserve the query and return matching results
    expect(results).to_contain_text("기후변화")
