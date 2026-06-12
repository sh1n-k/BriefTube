from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    get_db_connection,
    make_past_time,
    seed_app_setting,
    seed_article,
    seed_categories,
    seed_channel,
    seed_transcript,
    seed_video,
)

# ---------------------------------------------------------------------------
# Seed data constants
# ---------------------------------------------------------------------------

UC_ALPHA = "UC_ALPHA_CHANNEL_001"
UC_BETA = "UC_BETA_CHANNEL_002"

# 25 videos total: UC_ALPHA 15, UC_BETA 10
# Pipeline status distribution:
#   done            10  (ALPHA 1-7, BETA 1-3)
#   transcript_pending   5  (ALPHA 8-12)
#   llm_pending          3  (ALPHA 13-15)
#   transcript_failed    2  (BETA 4-5)
#   no_subtitle          2  (BETA 6-7)
#   llm_failed           2  (BETA 8-9)
#   manual_review        1  (BETA 10)

ALPHA_VIDEOS: list[tuple[str, str, str, int]] = (
    [
        # (video_id, title, pipeline_status, days_ago)
        (f"ALPHA_DONE_{i:02d}", f"Alpha Done Video {i}", "done", 30 - i)
        for i in range(1, 8)
    ]
    + [
        (f"ALPHA_TP_{i:02d}", f"Alpha Transcript Pending {i}", "transcript_pending", 20 - i)
        for i in range(1, 6)
    ]
    + [(f"ALPHA_LP_{i:02d}", f"Alpha LLM Pending {i}", "llm_pending", 10 - i) for i in range(1, 4)]
)

BETA_VIDEOS: list[tuple[str, str, str, int]] = (
    [(f"BETA_DONE_{i:02d}", f"Beta Done Video {i}", "done", 25 - i) for i in range(1, 4)]
    + [
        (f"BETA_TF_{i:02d}", f"Beta Transcript Failed {i}", "transcript_failed", 15 - i)
        for i in range(1, 3)
    ]
    + [(f"BETA_NS_{i:02d}", f"Beta No Subtitle {i}", "no_subtitle", 12 - i) for i in range(1, 3)]
    + [(f"BETA_LF_{i:02d}", f"Beta LLM Failed {i}", "llm_failed", 8 - i) for i in range(1, 3)]
    + [
        ("BETA_MR_01", "Beta Manual Review 1", "manual_review", 3),
    ]
)

# The 3 done videos that get transcript + article
DONE_WITH_ARTICLE = ["ALPHA_DONE_01", "ALPHA_DONE_02", "BETA_DONE_01"]


# ---------------------------------------------------------------------------
# Module-scoped fixture: seed data into the e2e_server DB
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_server(e2e_server: dict) -> dict:
    """Seed test data into the running e2e server's database, then return server info."""
    db_path = e2e_server["db_path"]

    # Categories
    cats = seed_categories(db_path)
    invest_cat_id = cats["투자"]
    default_cat_id = cats["미분류"]

    # Channels
    seed_channel(
        db_path,
        UC_ALPHA,
        "Alpha Channel",
        category_id=invest_cat_id,
    )
    seed_channel(
        db_path,
        UC_BETA,
        "Beta Channel",
        category_id=default_cat_id,
    )

    # Videos — Alpha
    for video_id, title, status, days_ago in ALPHA_VIDEOS:
        seed_video(
            db_path,
            video_id,
            UC_ALPHA,
            title,
            upload_time=make_past_time(days_ago),
            pipeline_status=status,
        )

    # Videos — Beta
    for video_id, title, status, days_ago in BETA_VIDEOS:
        seed_video(
            db_path,
            video_id,
            UC_BETA,
            title,
            upload_time=make_past_time(days_ago),
            pipeline_status=status,
        )

    # Transcripts + articles for 3 done videos
    for vid_id in DONE_WITH_ARTICLE:
        seed_transcript(
            db_path, vid_id, f"Transcript text for {vid_id}. This is sample subtitle content."
        )
        seed_article(
            db_path,
            vid_id,
            title=f"Article Title for {vid_id}",
            lead=f"Lead paragraph for {vid_id}.",
            body=f"Full article body for {vid_id}. Multiple sentences of content.",
        )

    # Videos per page = 10
    seed_app_setting(db_path, "videos_per_page", "10")

    return e2e_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _goto_home(page: Page, server: dict) -> None:
    page.goto(server["base_url"])
    page.wait_for_selector("#video-list-wrap")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_video_list_article_modal_scroll_resets_for_different_article(
    e2e_page: Page,
    seeded_server: dict,
) -> None:
    """목록 기사 모달은 같은 기사 재오픈만 스크롤을 유지한다."""
    page = e2e_page
    conn = get_db_connection(seeded_server["db_path"])
    try:
        for video_id in ("ALPHA_DONE_01", "BETA_DONE_01"):
            conn.execute(
                "UPDATE articles SET body = ? WHERE video_id = ?",
                (
                    "\n\n".join(
                        f"Full article body for {video_id}. Paragraph {idx}." for idx in range(80)
                    ),
                    video_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    page.goto(f"{seeded_server['base_url']}/?limit=25")
    page.wait_for_selector("#video-list-wrap")

    def open_article(video_id: str) -> None:
        page.locator("tr", has_text=video_id).locator("[data-video-article-preview-load]").click()
        expect(page.locator("[data-video-list-article-modal]")).to_be_visible()

    open_article("ALPHA_DONE_01")
    modal = page.locator("[data-video-list-article-modal]")
    scroller = modal.locator("[data-article-preview-scroll]")
    scroller.evaluate("(node) => { node.scrollTop = 360; }")
    assert scroller.evaluate("(node) => node.scrollTop") > 0
    modal.locator("[data-article-preview-close]").click()

    open_article("ALPHA_DONE_01")
    assert scroller.evaluate("(node) => node.scrollTop") > 0
    modal.locator("[data-article-preview-close]").click()

    open_article("BETA_DONE_01")
    assert scroller.evaluate("(node) => node.scrollTop") == 0


@pytest.mark.e2e
def test_video_list_pagination(e2e_page: Page, seeded_server: dict) -> None:
    """Page 1 shows 10 items; clicking next loads page 2 via HTMX swap."""
    page = e2e_page
    _goto_home(page, seeded_server)

    rows = page.locator("#video-list-wrap tbody tr")
    expect(rows).to_have_count(10)

    # Pagination info should show page 1 / 3 (25 total, 10 per page)
    pager_info = page.locator("#video-list-wrap .flex.items-center.justify-between.border-t p")
    expect(pager_info).to_contain_text("1 / 3")

    # Click next page button
    next_btn = page.locator("#video-list-wrap button[hx-get*='page=2']")
    expect(next_btn).to_be_visible()
    next_btn.click()

    # Wait for HTMX swap — the pager info should now show page 2
    page.wait_for_function(
        """() => {
            const p = document.querySelector('#video-list-wrap .flex.items-center.justify-between p');
            return p && p.textContent.includes('2 / 3');
        }"""
    )
    rows_p2 = page.locator("#video-list-wrap tbody tr")
    expect(rows_p2).to_have_count(10)


@pytest.mark.e2e
def test_video_detail_article_section(e2e_page: Page, seeded_server: dict) -> None:
    """기사 카드 raw 본문과 문서 보기 모달 렌더링을 함께 확인한다."""
    page = e2e_page
    video_id = "ALPHA_DONE_02"
    page.goto(f"{seeded_server['base_url']}/videos/{video_id}")
    page.wait_for_selector("#video-detail-wrap")

    article_card = page.locator("[data-detail-article-card]")
    expect(article_card).to_be_visible()
    article_body = article_card.locator("[data-collapsible-body]")
    if not article_body.is_visible():
        article_card.locator("[data-collapsible-toggle]").click()
    expect(article_body).to_be_visible()

    expect(article_card.locator("h3")).to_contain_text(f"Article Title for {video_id}")
    expect(article_card.locator("blockquote")).to_contain_text(f"Lead paragraph for {video_id}")
    expect(article_card.locator("div.article-rendered")).to_contain_text(
        f"Full article body for {video_id}"
    )

    article_card.locator("[data-article-preview-open]").click()
    modal = page.locator("#article-preview-modal")
    expect(modal).to_be_visible()
    expect(modal.locator("[data-article-preview-content].article-rendered")).to_contain_text(
        f"Full article body for {video_id}"
    )


@pytest.mark.e2e
def test_video_detail_auto_refresh_preserves_player_card(
    e2e_page: Page, seeded_server: dict
) -> None:
    """기사 대기 중 자동 갱신은 dynamic bundle만 교체하고 player card DOM은 유지된다."""
    page = e2e_page
    video_id = "ALPHA_LP_01"
    page.goto(f"{seeded_server['base_url']}/videos/{video_id}")
    page.wait_for_selector("#video-detail-wrap")

    fragment = page.locator("[data-video-detail-dynamic-fragment]")
    expect(fragment).to_have_attribute("data-video-detail-auto-refresh", "1")

    player_card = page.locator("[data-detail-player-card]")
    expect(player_card).to_be_visible()
    player_card.evaluate("(node) => node.setAttribute('data-player-stability-marker', 'kept')")

    with page.expect_response(
        lambda resp: (
            f"/views/videos/{video_id}/dynamic-fragment" in resp.url and resp.status == 200
        ),
        timeout=5_000,
    ):
        pass

    expect(page.locator("[data-detail-player-card]")).to_have_attribute(
        "data-player-stability-marker",
        "kept",
    )
    expect(page.locator("[data-detail-article-card]")).to_contain_text(
        re.compile(r"(아직 준비되지 않았습니다|Not ready)"),
    )


@pytest.mark.e2e
def test_video_detail_auto_refresh_skips_unchanged_fragment_swap(
    e2e_page: Page, seeded_server: dict
) -> None:
    """서버 응답이 같으면 dynamic bundle을 다시 갈아끼우지 않는다."""
    page = e2e_page
    video_id = "ALPHA_LP_01"
    page.goto(f"{seeded_server['base_url']}/videos/{video_id}")
    page.wait_for_selector("#video-detail-wrap")

    article_card = page.locator("[data-detail-article-card]")
    expect(article_card).to_be_visible()
    article_card.evaluate("(node) => node.setAttribute('data-refresh-stability-marker', 'kept')")

    with page.expect_response(
        lambda resp: (
            f"/views/videos/{video_id}/dynamic-fragment" in resp.url and resp.status == 200
        ),
        timeout=5_000,
    ):
        pass

    expect(page.locator("[data-detail-article-card]")).to_have_attribute(
        "data-refresh-stability-marker",
        "kept",
    )


@pytest.mark.e2e
def test_video_list_delete_selected(e2e_page: Page, seeded_server: dict) -> None:
    """Checking checkboxes and clicking delete removes video from list."""
    page = e2e_page
    _goto_home(page, seeded_server)

    # Get initial row count
    rows = page.locator("#video-list-wrap tbody tr")
    initial_count = rows.count()
    assert initial_count > 0

    # Get the first row target URL before delete
    first_row = page.locator("#video-list-wrap tbody tr").first
    target_href = first_row.locator("td a[href^='/videos/']").get_attribute("href")
    assert target_href is not None
    first_checkbox = first_row.locator("input[data-video-select-item]")
    first_checkbox.check()

    # The delete button (type="submit") should become enabled via JS
    delete_btn = page.locator("button[data-video-delete-selected]")
    expect(delete_btn).to_be_enabled(timeout=5000)

    # Click the submit button — triggers hx-post="/views/videos/delete-selected"
    with page.expect_response("**/views/videos/delete-selected") as resp_info:
        delete_btn.click()
    assert resp_info.value.ok

    # After HTMX swap, verify the deleted target is not present on the page.
    page.wait_for_selector("#video-list-wrap tbody tr")
    assert page.locator(f"#video-list-wrap tbody a[href='{target_href}']").count() == 0
