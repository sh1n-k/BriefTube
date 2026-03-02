from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
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

ALPHA_VIDEOS: list[tuple[str, str, str, int]] = [
    # (video_id, title, pipeline_status, days_ago)
    (f"ALPHA_DONE_{i:02d}", f"Alpha Done Video {i}", "done", 30 - i)
    for i in range(1, 8)
] + [
    (f"ALPHA_TP_{i:02d}", f"Alpha Transcript Pending {i}", "transcript_pending", 20 - i)
    for i in range(1, 6)
] + [
    (f"ALPHA_LP_{i:02d}", f"Alpha LLM Pending {i}", "llm_pending", 10 - i)
    for i in range(1, 4)
]

BETA_VIDEOS: list[tuple[str, str, str, int]] = [
    (f"BETA_DONE_{i:02d}", f"Beta Done Video {i}", "done", 25 - i)
    for i in range(1, 4)
] + [
    (f"BETA_TF_{i:02d}", f"Beta Transcript Failed {i}", "transcript_failed", 15 - i)
    for i in range(1, 3)
] + [
    (f"BETA_NS_{i:02d}", f"Beta No Subtitle {i}", "no_subtitle", 12 - i)
    for i in range(1, 3)
] + [
    (f"BETA_LF_{i:02d}", f"Beta LLM Failed {i}", "llm_failed", 8 - i)
    for i in range(1, 3)
] + [
    ("BETA_MR_01", "Beta Manual Review 1", "manual_review", 3),
]

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
        db_path, UC_ALPHA, "Alpha Channel", category_id=invest_cat_id,
    )
    seed_channel(
        db_path, UC_BETA, "Beta Channel", category_id=default_cat_id,
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
        seed_transcript(db_path, vid_id, f"Transcript text for {vid_id}. This is sample subtitle content.")
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
def test_home_page_loads(e2e_page: Page, seeded_server: dict) -> None:
    """GET / loads with title containing BriefTube, nav links, and video list."""
    page = e2e_page
    _goto_home(page, seeded_server)

    expect(page).to_have_title(re.compile(r"BriefTube"))

    # Nav links
    nav = page.locator("header nav")
    expect(nav.locator("a", has_text=re.compile(r"(영상|Videos)"))).to_be_visible()
    expect(nav.locator("a", has_text=re.compile(r"(채널|Channels)"))).to_be_visible()
    expect(nav.locator("a", has_text=re.compile(r"(설정|Settings)"))).to_be_visible()

    # Video list wrapper
    expect(page.locator("#video-list-wrap")).to_be_visible()


@pytest.mark.e2e
def test_video_list_shows_videos(e2e_page: Page, seeded_server: dict) -> None:
    """Video cards display title, channel name, and status badge."""
    page = e2e_page
    _goto_home(page, seeded_server)

    rows = page.locator("#video-list-wrap tbody tr")
    # With 10 per page and 25 total, we should see 10 rows on page 1
    expect(rows).to_have_count(10)

    # The first row should have a video title link
    first_row = rows.first
    title_link = first_row.locator("td a[href^='/videos/']")
    expect(title_link).to_be_visible()

    # Channel name should be visible
    channel_cell = first_row.locator("td:nth-child(4)")
    expect(channel_cell).to_contain_text(re.compile(r"(Alpha|Beta) Channel"))

    # Status badge should be visible
    badge = first_row.locator(".status-badge")
    expect(badge).to_be_visible()


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
    next_btn = page.locator(
        "#video-list-wrap button[hx-get*='page=2']"
    )
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
def test_video_list_channel_filter(e2e_page: Page, seeded_server: dict) -> None:
    """Selecting a channel from the dropdown filters the video list."""
    page = e2e_page
    _goto_home(page, seeded_server)

    # Select "Alpha Channel" in the channel dropdown
    channel_select = page.locator("#video-list-wrap select[name='channel_id']")
    expect(channel_select).to_be_visible()
    channel_select.select_option(label="Alpha Channel")

    # Wait for HTMX swap — all visible videos should belong to Alpha Channel
    page.wait_for_function(
        """() => {
            const cells = document.querySelectorAll('#video-list-wrap tbody td:nth-child(4)');
            if (cells.length === 0) return false;
            return Array.from(cells).every(c => c.textContent.includes('Alpha Channel'));
        }"""
    )

    rows = page.locator("#video-list-wrap tbody tr")
    count = rows.count()
    assert count > 0
    for i in range(count):
        channel_cell = rows.nth(i).locator("td:nth-child(4)")
        expect(channel_cell).to_contain_text("Alpha Channel")


@pytest.mark.e2e
def test_video_list_category_filter(e2e_page: Page, seeded_server: dict) -> None:
    """Selecting category filters to only that category's channels."""
    page = e2e_page
    _goto_home(page, seeded_server)

    # Select the "투자" category
    cat_select = page.locator("#video-list-wrap select[name='category_id']")
    expect(cat_select).to_be_visible()

    # Find the 투자 option value from the dropdown options
    options = cat_select.locator("option")
    invest_value = None
    for i in range(options.count()):
        if "투자" in (options.nth(i).inner_text() or ""):
            invest_value = options.nth(i).get_attribute("value")
            break
    assert invest_value is not None, "투자 option not found in category dropdown"
    cat_select.select_option(value=invest_value)

    # Wait for HTMX swap — only Alpha Channel videos should appear (투자 category)
    page.wait_for_function(
        """() => {
            const cells = document.querySelectorAll('#video-list-wrap tbody td:nth-child(4)');
            if (cells.length === 0) return false;
            return Array.from(cells).every(c => c.textContent.includes('Alpha Channel'));
        }"""
    )

    rows = page.locator("#video-list-wrap tbody tr")
    count = rows.count()
    assert count > 0
    for i in range(count):
        channel_cell = rows.nth(i).locator("td:nth-child(4)")
        expect(channel_cell).to_contain_text("Alpha Channel")


@pytest.mark.e2e
def test_video_list_sort_order(e2e_page: Page, seeded_server: dict) -> None:
    """Clicking the upload column header toggles sort order."""
    page = e2e_page
    _goto_home(page, seeded_server)

    # Default sort is upload_time desc. Collect first video title.
    first_title_desc = (
        page.locator("#video-list-wrap tbody tr")
        .first.locator("td a[href^='/videos/']")
        .inner_text()
    )

    # Click the sortable upload column header to toggle to asc
    upload_header = page.locator(
        "#video-list-wrap th[hx-get*='order=asc']"
    )
    if upload_header.count() == 0:
        # Already asc, look for desc toggle
        upload_header = page.locator(
            "#video-list-wrap th[hx-get*='order=desc']"
        )
    with page.expect_response(lambda resp: "/views/video-list" in resp.url and resp.status == 200):
        upload_header.click()
    page.wait_for_selector("#video-list-wrap tbody tr")

    first_title_toggled = (
        page.locator("#video-list-wrap tbody tr")
        .first.locator("td a[href^='/videos/']")
        .inner_text()
    )

    # After toggling, the first video should be different
    assert first_title_desc != first_title_toggled

    # Toggle again and ensure we return to the original first row.
    second_toggle = page.locator("#video-list-wrap th[hx-get*='order=']").first
    with page.expect_response(lambda resp: "/views/video-list" in resp.url and resp.status == 200):
        second_toggle.click()
    first_title_restored = (
        page.locator("#video-list-wrap tbody tr")
        .first.locator("td a[href^='/videos/']")
        .inner_text()
    )
    assert first_title_restored == first_title_desc


@pytest.mark.e2e
def test_video_detail_page(e2e_page: Page, seeded_server: dict) -> None:
    """Clicking a video navigates to /videos/{id} with title, channel, and badge."""
    page = e2e_page
    _goto_home(page, seeded_server)

    # Click the first video link
    first_link = page.locator("#video-list-wrap tbody tr a[href^='/videos/']").first
    video_title = first_link.inner_text()
    first_link.click()

    # Should be on the detail page
    page.wait_for_selector("#video-detail-wrap")
    expect(page).to_have_url(re.compile(r"/videos/"))

    # Meta card should show the title
    meta_card = page.locator("[data-detail-meta-card]")
    expect(meta_card).to_be_visible()
    expect(meta_card.locator("h1")).to_contain_text(video_title)

    # Channel name
    expect(meta_card).to_contain_text(re.compile(r"(Alpha|Beta) Channel"))

    # Status badge
    expect(meta_card.locator(".status-badge")).to_be_visible()


@pytest.mark.e2e
def test_video_detail_transcript_section(e2e_page: Page, seeded_server: dict) -> None:
    """A done video with transcript shows transcript text on the detail page."""
    page = e2e_page
    video_id = "ALPHA_DONE_01"
    page.goto(f"{seeded_server['base_url']}/videos/{video_id}")
    page.wait_for_selector("#video-detail-wrap")

    # Transcript section is inside a collapsible — click to expand first
    transcript_toggle = page.locator(
        "section[data-collapsible]:has(#transcript-copy-source) [data-collapsible-toggle]"
    )
    transcript_toggle.click()

    # Now the transcript text should be visible
    transcript_pre = page.locator("#transcript-copy-source")
    expect(transcript_pre).to_be_visible()
    expect(transcript_pre).to_contain_text(f"Transcript text for {video_id}")


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
    expect(article_card.locator("pre")).to_contain_text(f"Full article body for {video_id}")

    article_card.locator("[data-article-preview-open]").click()
    modal = page.locator("#article-preview-modal")
    expect(modal).to_be_visible()
    expect(modal.locator("[data-article-preview-content].article-rendered")).to_contain_text(
        f"Full article body for {video_id}"
    )


@pytest.mark.e2e
def test_video_detail_status_badge_styles(e2e_page: Page, seeded_server: dict) -> None:
    """Status badges use the correct CSS class per pipeline_status."""
    page = e2e_page

    test_cases = [
        ("ALPHA_DONE_01", "status-badge--done"),
        ("ALPHA_TP_01", "status-badge--transcript-pending"),
        ("ALPHA_LP_01", "status-badge--llm-pending"),
        ("BETA_TF_01", "status-badge--transcript-failed"),
        ("BETA_NS_01", "status-badge--no-subtitle"),
        ("BETA_LF_01", "status-badge--llm-failed"),
        ("BETA_MR_01", "status-badge--manual-review"),
    ]

    for video_id, expected_class in test_cases:
        page.goto(f"{seeded_server['base_url']}/videos/{video_id}")
        page.wait_for_selector("#video-detail-wrap")
        badge = page.locator("[data-detail-meta-card] .status-badge")
        expect(badge).to_be_visible()
        expect(badge).to_have_class(re.compile(re.escape(expected_class)))


@pytest.mark.e2e
def test_video_list_search(e2e_page: Page, seeded_server: dict) -> None:
    """Typing in the search box triggers HTMX update to #search-results."""
    page = e2e_page
    _goto_home(page, seeded_server)

    # The search input
    search_input = page.locator("input[name='q']")
    expect(search_input).to_be_visible()

    # Type a query that should match a transcript
    search_input.fill("Transcript text for ALPHA_DONE_01")
    search_input.press("Enter")

    # Wait for #search-results to contain results
    page.wait_for_function(
        """() => {
            const el = document.querySelector('#search-results');
            return el && el.textContent.trim().length > 10;
        }"""
    )

    search_results = page.locator("#search-results")
    expect(search_results).to_be_visible()
    # Should contain a link to the matching video
    expect(search_results.locator("a[href*='ALPHA_DONE_01']")).to_be_visible()


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


@pytest.mark.e2e
def test_video_list_empty_state(e2e_page: Page, seeded_server: dict) -> None:
    """When filtering yields no results, an empty state message appears."""
    page = e2e_page

    # Navigate to video-list with a nonexistent channel_id to force empty state
    page.goto(
        f"{seeded_server['base_url']}/?channel_id=UC_NONEXISTENT_CHANNEL"
    )
    page.wait_for_selector("#video-list-wrap")

    # The empty state has colspan="6" cell with the empty message
    empty_cell = page.locator("#video-list-wrap td[colspan='6']")
    expect(empty_cell).to_be_visible()
    # Check for the empty title text (ko or en)
    expect(empty_cell).to_contain_text(
        re.compile(r"(영상이 없습니다|No videos yet)")
    )


@pytest.mark.e2e
def test_video_thumbnail_fallback(e2e_page: Page, seeded_server: dict) -> None:
    """When thumbnail_path is NULL, YouTube CDN URL is rendered as the img src."""
    page = e2e_page
    _goto_home(page, seeded_server)

    # Our seeded videos have no thumbnail_path, so _thumbnail_url falls back to
    # https://i.ytimg.com/vi/{video_id}/hqdefault.jpg
    first_row = page.locator("#video-list-wrap tbody tr").first
    first_link = first_row.locator("td a[href^='/videos/']").first
    first_href = first_link.get_attribute("href")
    assert first_href is not None and first_href.startswith("/videos/")
    first_video_id = first_href.removeprefix("/videos/")
    first_img = first_row.locator("img[alt='thumbnail']").first
    expect(first_img).to_be_visible()

    src = first_img.get_attribute("src")
    assert src is not None
    assert "i.ytimg.com" in src
    assert f"/{first_video_id}/" in src
    assert "hqdefault.jpg" in src


@pytest.mark.e2e
def test_poll_now_button(e2e_page: Page, seeded_server: dict) -> None:
    """Clicking the poll now button fires a POST to /api/poll/trigger."""
    page = e2e_page
    _goto_home(page, seeded_server)

    # The poll now button uses hx-post="/api/poll/trigger"
    poll_btn = page.locator("button[hx-post='/api/poll/trigger']")
    expect(poll_btn).to_be_visible()

    # Intercept the network request to verify
    with page.expect_request("**/api/poll/trigger") as request_info:
        poll_btn.click()

    request = request_info.value
    assert request.method == "POST"
    assert "/api/poll/trigger" in request.url
