from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    get_db_connection,
    seed_categories,
    seed_channel,
    seed_video,
    disable_all_workers,
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
    page.wait_for_load_state("networkidle")


def _parse_search_index(text: str) -> tuple[int, int]:
    matched = re.search(r"(\d+)\s*/\s*(\d+)", text)
    assert matched is not None, f"invalid search count text: {text}"
    return int(matched.group(1)), int(matched.group(2))


# ===================================================================
# Tests
# ===================================================================


@pytest.mark.e2e
def test_channels_page_loads(page: Page) -> None:
    """/channels loads, title includes '채널', channel list visible."""
    _goto_channels(page)

    # Page title text
    h1 = page.locator("h1")
    expect(h1).to_contain_text("채널")

    # Channel list wrapper is visible
    channel_list_wrap = page.locator("#channel-list-wrap")
    expect(channel_list_wrap).to_be_visible()

    # At least one channel row rendered
    rows = page.locator("[data-channel-row]")
    expect(rows.first).to_be_visible()
    assert rows.count() == len(ACTIVE_CHANNELS)


@pytest.mark.e2e
def test_channels_active_inactive_tabs(page: Page) -> None:
    """Tab switch shows different channels; count badges show correct numbers."""
    _goto_channels(page, status="active")

    # Active tab has count badge (use the tab bar link, not sidebar)
    tab_bar = page.locator("div.inline-flex.rounded-lg")
    active_tab = tab_bar.locator("a[href='/channels?status=active']")
    expect(active_tab).to_be_visible()
    active_badge = active_tab.locator("span")
    expect(active_badge).to_contain_text(str(len(ACTIVE_CHANNELS)))

    # Inactive tab has count badge
    inactive_tab = tab_bar.locator("a[href='/channels?status=inactive']")
    expect(inactive_tab).to_be_visible()
    inactive_badge = inactive_tab.locator("span")
    expect(inactive_badge).to_contain_text(str(len(INACTIVE_CHANNELS)))

    # Switch to inactive tab
    inactive_tab.click()
    page.wait_for_load_state("networkidle")

    # Verify inactive channels are shown
    rows = page.locator("[data-channel-row]")
    assert rows.count() == len(INACTIVE_CHANNELS)


@pytest.mark.e2e
def test_channel_search_filter(page: Page) -> None:
    """Search input filters channels via JS matching, highlights matches."""
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


@pytest.mark.e2e
def test_channel_search_prev_next(page: Page) -> None:
    """Prev/next navigation cycles through matching channels."""
    _goto_channels(page)

    search_input = page.locator("[data-channel-search-input]")
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
def test_channel_add_form(page: Page) -> None:
    """URL input submit shows result in #channel-add-result via HTMX."""
    _goto_channels(page)

    # Expand the compose section if collapsed
    compose_body = page.locator("#channel-compose-body")
    if not compose_body.is_visible():
        page.locator("[data-channel-compose-toggle]").click()
        expect(compose_body).to_be_visible()

    # Fill empty input and submit to trigger validation error
    source_input = page.locator("input[name='source']")
    source_input.fill("")

    submit_btn = page.locator(
        "form[hx-post='/views/channels/add'] button[type='submit'], "
        "form[hx-post='/views/channels/add'] button:not([type])"
    ).first
    submit_btn.click()

    # Wait for HTMX response
    result_area = page.locator("#channel-add-result")
    expect(result_area).not_to_be_empty(timeout=10_000)

    # Should contain error message (empty input)
    expect(result_area).to_contain_text("입력")


@pytest.mark.e2e
def test_channel_delete_single(page: Page) -> None:
    """Delete button removes a single channel from the list."""
    _goto_channels(page)

    # Count channels before
    rows_before = page.locator("[data-channel-row]").count()
    assert rows_before >= 1

    # Click delete button for the first channel
    first_delete = page.locator(
        "button[hx-post*='/delete']"
    ).first
    first_delete.click()

    expected_rows = rows_before - 1
    expect(page.locator("[data-channel-row]")).to_have_count(expected_rows)


@pytest.mark.e2e
def test_channel_delete_bulk(page: Page) -> None:
    """Select all + delete removes all currently listed channels."""
    _goto_channels(page)

    rows_before = page.locator("[data-channel-row]").count()
    assert rows_before >= 1

    # Check select-all
    select_all = page.locator("[data-channel-select-all]")
    select_all.check()

    # Bulk delete button should be enabled
    bulk_delete_btn = page.locator(
        "form[data-channel-manage-form] button[data-channel-bulk-submit]"
    ).first
    expect(bulk_delete_btn).to_be_enabled()

    bulk_delete_btn.click()

    page.wait_for_function(
        "() => document.querySelectorAll('[data-channel-row]').length === 0",
    )
    assert page.locator("[data-channel-row]").count() == 0


@pytest.mark.e2e
def test_channel_select_all_toggle(page: Page) -> None:
    """Select-all checkbox toggles all channel checkboxes."""
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


@pytest.mark.e2e
def test_channel_compose_accordion(page: Page) -> None:
    """Channel compose area expand/collapse via toggle button."""
    _goto_channels(page)

    toggle = page.locator("[data-channel-compose-toggle]")
    body = page.locator("#channel-compose-body")

    # Ensure initially expanded (default behavior)
    expect(toggle).to_be_visible()

    if body.is_visible():
        # Collapse
        toggle.click()
        page.wait_for_timeout(300)
        expect(body).to_be_hidden()

        # Expand again
        toggle.click()
        page.wait_for_timeout(300)
        expect(body).to_be_visible()
    else:
        # Expand
        toggle.click()
        page.wait_for_timeout(300)
        expect(body).to_be_visible()

        # Collapse
        toggle.click()
        page.wait_for_timeout(300)
        expect(body).to_be_hidden()


@pytest.mark.e2e
def test_channel_bulk_resolve_form(page: Page) -> None:
    """Bulk text submission triggers resolve and shows #bulk-resolve-result."""
    _goto_channels(page)

    # Expand compose if needed
    compose_body = page.locator("#channel-compose-body")
    if not compose_body.is_visible():
        page.locator("[data-channel-compose-toggle]").click()
        expect(compose_body).to_be_visible()

    # Fill bulk text area with dummy content
    textarea = page.locator("textarea[name='bulk_text']")
    expect(textarea).to_be_visible()
    textarea.fill("https://www.youtube.com/@testchannel123\nhttps://www.youtube.com/@anotherchannel")

    # Click resolve button
    resolve_btn = page.locator(
        "form[hx-post='/views/channels/bulk-resolve'] button[type='submit'], "
        "form[hx-post='/views/channels/bulk-resolve'] button:not([type])"
    ).first
    resolve_btn.click()

    # Wait for HTMX to populate the result area
    result_area = page.locator("#bulk-resolve-result")
    expect(result_area).not_to_be_empty(timeout=15_000)

    # Should show some result structure
    expect(result_area).to_contain_text("Total")


@pytest.mark.e2e
def test_channel_category_sidebar_filter(page: Page) -> None:
    """Click a category in sidebar filters the channel list via HTMX."""
    _goto_channels(page)

    sidebar = page.locator("#category-sidebar")
    expect(sidebar).to_be_visible()

    # Click '투자' category from the sidebar (real user flow)
    invest_id = page._e2e_server["categories"]["투자"]
    invest_link = sidebar.locator(f"a[href='/channels?status=active&category_id={invest_id}']").first
    expect(invest_link).to_be_visible()
    with page.expect_response(
        lambda res: (
            "/views/channel-list" in res.url
            and f"status=active&category_id={invest_id}" in res.url
            and res.ok
        )
    ):
        invest_link.click()

    rows = page.locator("[data-channel-row]")
    expect(rows).to_have_count(2)
    for i in range(rows.count()):
        expect(rows.nth(i)).to_contain_text("투자채널")


@pytest.mark.e2e
def test_channel_category_all_restores(page: Page) -> None:
    """Clicking 'all channels' in sidebar shows all channels."""
    _goto_channels(page)

    sidebar = page.locator("#category-sidebar")
    invest_id = page._e2e_server["categories"]["투자"]
    invest_link = sidebar.locator(f"a[href='/channels?status=active&category_id={invest_id}']").first
    with page.expect_response(
        lambda res: (
            "/views/channel-list" in res.url
            and f"status=active&category_id={invest_id}" in res.url
            and res.ok
        )
    ):
        invest_link.click()
    all_link = sidebar.locator("a[href='/channels?status=active']").first
    with page.expect_response(
        lambda res: (
            "/views/channel-list" in res.url
            and "status=active" in res.url
            and "category_id=" not in res.url
            and res.ok
        )
    ):
        all_link.click()

    rows = page.locator("[data-channel-row]")
    expect(rows).to_have_count(len(ACTIVE_CHANNELS))


@pytest.mark.e2e
def test_channel_metadata_accordion(page: Page) -> None:
    """Meta accordion mutual exclusion: opening one closes others."""
    _goto_channels(page)

    items = page.locator("[data-channel-meta-item]")
    if items.count() < 2:
        pytest.skip("Need at least 2 channels for accordion test")

    # Click first toggle
    toggle_1 = items.nth(0).locator("[data-channel-meta-toggle]")
    panel_1 = items.nth(0).locator("[data-channel-meta-panel]")
    toggle_1.click()
    page.wait_for_timeout(300)
    expect(panel_1).to_be_visible()

    # Click second toggle
    toggle_2 = items.nth(1).locator("[data-channel-meta-toggle]")
    panel_2 = items.nth(1).locator("[data-channel-meta-panel]")
    toggle_2.click()
    page.wait_for_timeout(300)

    # Second should open, first should close (mutual exclusion)
    expect(panel_2).to_be_visible()
    expect(panel_1).to_be_hidden()


@pytest.mark.e2e
def test_channel_category_sidebar_add(page: Page) -> None:
    """Adding a category updates the sidebar."""
    _goto_channels(page)

    sidebar = page.locator("#category-sidebar")
    expect(sidebar).to_be_visible()

    # Count existing categories in sidebar list
    cat_items_before = sidebar.locator("[data-category-list] li").count()

    # Fill the add category form
    name_input = sidebar.locator("input[name='name']")
    expect(name_input).to_be_visible()
    name_input.fill("E2E테스트카테고리")

    add_btn = sidebar.locator("form[hx-post='/views/categories'] button[type='submit']")
    add_btn.click()

    page.wait_for_function(
        "() => document.querySelector('#category-sidebar')?.textContent?.includes('E2E테스트카테고리') === true",
    )

    # Sidebar should now contain the new category
    sidebar_updated = page.locator("#category-sidebar")
    expect(sidebar_updated).to_contain_text("E2E테스트카테고리")

    cat_items_after = sidebar_updated.locator("[data-category-list] li").count()
    assert cat_items_after == cat_items_before + 1


@pytest.mark.e2e
def test_channel_category_sidebar_delete(page: Page) -> None:
    """Category delete with hx-confirm updates sidebar."""
    _goto_channels(page)

    sidebar = page.locator("#category-sidebar")
    name_input = sidebar.locator("input[name='name']")
    name_input.fill("삭제테스트카테고리")
    sidebar.locator("form[hx-post='/views/categories'] button[type='submit']").click()
    expect(sidebar).to_contain_text("삭제테스트카테고리")
    sidebar = page.locator("#category-sidebar")
    target_selector = "[data-category-list] li:has-text('삭제테스트카테고리')"
    target_item = sidebar.locator(target_selector)
    expect(target_item).to_have_count(1)
    delete_btn = target_item.locator("button[hx-delete]")

    # hx-confirm will trigger a browser dialog
    page.once("dialog", lambda dialog: dialog.accept())

    with page.expect_response(
        lambda res: ("/views/categories/" in res.url and res.request.method == "DELETE" and res.ok)
    ):
        delete_btn.click(force=True)
    expect(page.locator(target_selector)).to_have_count(0)


@pytest.mark.e2e
def test_channel_reactivate_flow(page: Page) -> None:
    """Inactive tab -> select channels -> reactivate button -> confirm modal."""
    _goto_channels(page, status="inactive")

    rows = page.locator("[data-channel-row]")
    if rows.count() == 0:
        pytest.skip("No inactive channels to test reactivation")

    # Check the first inactive channel
    first_item = page.locator("[data-channel-select-item]").first
    first_item.check()

    # The reactivate button should exist on the inactive tab
    reactivate_btn = page.locator(
        "button[data-channel-bulk-submit]:not([name='bulk_action'])"
    ).first

    expect(reactivate_btn).to_be_visible()
    expect(reactivate_btn).to_contain_text("재활성화")

    reactivate_btn.click()

    # A custom confirm modal should appear (not browser dialog)
    confirm_modal = page.locator("#channel-reactivate-confirm-modal")
    expect(confirm_modal).to_be_visible(timeout=5_000)

    # Modal should have title text
    title_node = confirm_modal.locator("[data-reactivate-confirm-title]")
    expect(title_node).to_be_visible()

    # Click cancel to dismiss
    cancel_btn = confirm_modal.locator("[data-reactivate-confirm-cancel]")
    cancel_btn.click()

    # Modal should be hidden
    expect(confirm_modal).to_be_hidden()
