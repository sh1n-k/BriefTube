from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    disable_all_workers,
    get_db_connection,
    seed_categories,
    seed_channel,
    seed_video,
)

# ---------------------------------------------------------------------------
# Seed data constants
# ---------------------------------------------------------------------------

ACTIVE_CHANNELS = [
    ("UC_INVEST_01", "투자채널A", "투자"),
    ("UC_INVEST_02", "투자채널B", "투자"),
    ("UC_TECH_01", "기술채널A", "기술"),
    ("UC_TECH_02", "기술채널B", "기술"),
    ("UC_DEFAULT_01", "미분류채널A", "미분류"),
]

INACTIVE_CHANNELS = [
    ("UC_INACTIVE_01", "비활성채널A", "투자"),
    ("UC_INACTIVE_02", "비활성채널B", "기술"),
]


def _seed_all(db_path: str) -> dict[str, int]:
    """Seed 3 categories, 5 active + 2 inactive channels, and videos."""
    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM videos")
    conn.execute("DELETE FROM channels")
    conn.execute("DELETE FROM categories WHERE is_default = 0")
    conn.commit()
    conn.close()

    cats = seed_categories(db_path)
    disable_all_workers(db_path)

    for ch_id, ch_name, cat_name in ACTIVE_CHANNELS:
        seed_channel(
            db_path,
            ch_id,
            ch_name,
            is_active=1,
            category_id=cats[cat_name],
        )

    for ch_id, ch_name, cat_name in INACTIVE_CHANNELS:
        seed_channel(
            db_path,
            ch_id,
            ch_name,
            is_active=0,
            category_id=cats[cat_name],
        )

    # 2-3 videos per active channel
    vid_counter = 0
    for ch_id, _ch_name, _cat_name in ACTIVE_CHANNELS:
        for j in range(3 if vid_counter % 2 == 0 else 2):
            vid_counter += 1
            seed_video(
                db_path,
                f"VID_{vid_counter:03d}",
                ch_id,
                f"Test Video {vid_counter}",
            )

    # 2 videos per inactive channel
    for ch_id, _ch_name, _cat_name in INACTIVE_CHANNELS:
        for j in range(2):
            vid_counter += 1
            seed_video(
                db_path,
                f"VID_{vid_counter:03d}",
                ch_id,
                f"Inactive Video {vid_counter}",
            )

    return cats


# ---------------------------------------------------------------------------
# Module-scoped seeded server fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_server(e2e_server: dict) -> dict:
    """Seed data after the server starts, return server dict + categories."""
    cats = _seed_all(e2e_server["db_path"])
    return {**e2e_server, "categories": cats}


@pytest.fixture()
def page(seeded_server: dict, context) -> Page:
    """Playwright page wired to the seeded server."""
    pg = context.new_page()
    pg.set_default_timeout(10_000)
    pg.set_default_navigation_timeout(15_000)
    pg._e2e_base_url = seeded_server["base_url"]
    pg._e2e_db_path = seeded_server["db_path"]
    pg._e2e_server = seeded_server
    yield pg
    pg.close()


def _goto_channels(page: Page, *, status: str = "active", **params: str) -> None:
    qs = f"status={status}"
    for k, v in params.items():
        qs += f"&{k}={v}"
    page.goto(f"{page._e2e_base_url}/channels?{qs}")
    page.wait_for_selector("#channel-list-wrap")


def _parse_search_index(text: str) -> tuple[int, int]:
    matched = re.search(r"(\d+)\s*/\s*(\d+)", text)
    assert matched is not None, f"invalid search count text: {text}"
    return int(matched.group(1)), int(matched.group(2))


# ===================================================================
# Tests
# ===================================================================


@pytest.mark.e2e
def test_channel_search_filter_and_navigation(page: Page) -> None:
    """Search filters, highlights, and cycles through matching channels."""
    _goto_channels(page)

    search_input = page.locator("[data-channel-search-input]")
    expect(search_input).to_be_visible()

    # Type a search that matches only '투자' channels
    search_input.fill("투자")

    # Count indicator should appear
    count_node = page.locator("[data-channel-search-count]")
    expect(count_node).to_have_text(re.compile(r"\d+\s*/\s*\d+"))

    # Verify matched rows have highlight class (uses bg-amber-50, ring-1 from main-ui.js)
    highlighted = page.locator("[data-channel-row].bg-amber-50, [data-channel-row].bg-indigo-100")
    assert highlighted.count() >= 1

    search_input.fill("채널")

    next_btn = page.locator("[data-channel-search-next]")
    prev_btn = page.locator("[data-channel-search-prev]")

    expect(next_btn).to_be_visible()
    expect(prev_btn).to_be_visible()

    count_node = page.locator("[data-channel-search-count]")
    first_index, total = _parse_search_index(count_node.inner_text())
    assert total >= 2

    next_btn.click()
    expect(count_node).not_to_have_text(f"{first_index} / {total}")
    second_index, second_total = _parse_search_index(count_node.inner_text())
    assert second_total == total
    assert second_index != first_index

    prev_btn.click()
    expect(count_node).to_have_text(re.compile(rf"{first_index}\s*/\s*{total}"))


@pytest.mark.e2e
def test_channel_select_all_and_reactivate_modal(page: Page) -> None:
    """Select-all works and inactive selections open the reactivate modal."""
    _goto_channels(page)

    select_all = page.locator("[data-channel-select-all]")
    items = page.locator("[data-channel-select-item]")

    # Check select-all
    select_all.check()

    for i in range(items.count()):
        expect(items.nth(i)).to_be_checked()

    # Uncheck select-all
    select_all.uncheck()

    for i in range(items.count()):
        expect(items.nth(i)).not_to_be_checked()

    _goto_channels(page, status="inactive")
    first_item = page.locator("[data-channel-select-item]").first
    first_item.check()

    reactivate_btn = page.locator(
        "button[data-channel-bulk-submit]:not([name='bulk_action'])"
    ).first
    expect(reactivate_btn).to_be_visible()
    expect(reactivate_btn).to_contain_text("재활성화")
    reactivate_btn.click()

    confirm_modal = page.locator("#channel-reactivate-confirm-modal")
    expect(confirm_modal).to_be_visible(timeout=5_000)
    expect(confirm_modal.locator("[data-reactivate-confirm-title]")).to_be_visible()
    confirm_modal.locator("[data-reactivate-confirm-cancel]").click()
    expect(confirm_modal).to_be_hidden()


@pytest.mark.e2e
def test_channel_accordions(page: Page) -> None:
    """Compose and metadata accordions expand, collapse, and exclude peers."""
    _goto_channels(page)

    toggle = page.locator("[data-channel-compose-toggle]")
    body = page.locator("#channel-compose-body")

    # Default is collapsed; expand/collapse still works.
    expect(toggle).to_be_visible()
    expect(body).to_be_hidden()
    toggle.click()
    expect(body).to_be_visible()
    toggle.click()
    expect(body).to_be_hidden()

    items = page.locator("[data-channel-meta-item]")
    if items.count() < 2:
        pytest.skip("Need at least 2 channels for accordion test")

    # Click first toggle
    toggle_1 = items.nth(0).locator("[data-channel-meta-toggle]")
    panel_1 = items.nth(0).locator("[data-channel-meta-panel]")
    toggle_1.click()
    expect(panel_1).to_be_visible()

    # Click second toggle
    toggle_2 = items.nth(1).locator("[data-channel-meta-toggle]")
    panel_2 = items.nth(1).locator("[data-channel-meta-panel]")
    toggle_2.click()

    # Second should open, first should close (mutual exclusion)
    expect(panel_2).to_be_visible()
    expect(panel_1).to_be_hidden()


@pytest.mark.e2e
def test_channel_category_filter_keeps_full_page_url_after_reload(page: Page) -> None:
    """Category HTMX filter must push /channels, not the fragment URL."""
    _goto_channels(page)
    category_id = str(page._e2e_server["categories"]["투자"])
    category_link = page.locator(f"#category-sidebar a[href*='category_id={category_id}']").first

    category_link.click()

    expect(page).to_have_url(re.compile(r"/channels\?.*category_id="))
    parsed = urlparse(page.url)
    query = parse_qs(parsed.query)
    assert parsed.path == "/channels"
    assert "/views/" not in page.url
    assert query.get("status") == ["active"]
    assert query.get("category_id") == [category_id]

    page.reload()
    page.wait_for_selector("#category-sidebar")
    expect(page.locator("#channel-list-wrap")).to_be_visible()
    expect(page.locator("body")).to_contain_text("투자채널A")
