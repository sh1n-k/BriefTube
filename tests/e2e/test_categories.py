from __future__ import annotations

"""E2E tests for the category management feature on the channels page."""


import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    get_db_connection,
    seed_categories,
    seed_channel,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CHANNELS = [
    # (channel_id, channel_name, category_key)
    ("UC_default_1", "Default Channel A", "미분류"),
    ("UC_default_2", "Default Channel B", "미분류"),
    ("UC_invest_1", "Invest Channel A", "투자"),
    ("UC_invest_2", "Invest Channel B", "투자"),
    ("UC_tech_1", "Tech Channel A", "기술"),
    ("UC_tech_2", "Tech Channel B", "기술"),
]


def _clean_seed_data(db_path: str) -> None:
    """Remove all non-default categories, channels, and videos to ensure a clean state."""
    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM videos")
    conn.execute("DELETE FROM channels")
    conn.execute("DELETE FROM categories WHERE is_default = 0")
    conn.commit()
    conn.close()


@pytest.fixture()
def seeded_page(e2e_page: Page) -> Page:
    """Seed 3 categories + 6 channels, then navigate to /channels."""
    db = e2e_page._e2e_db_path
    _clean_seed_data(db)
    cat_ids = seed_categories(db)

    for ch_id, ch_name, cat_key in CHANNELS:
        seed_channel(db, ch_id, ch_name, category_id=cat_ids[cat_key])

    e2e_page.goto(f"{e2e_page._e2e_base_url}/channels")
    e2e_page.wait_for_selector("#category-sidebar")
    return e2e_page


# ---------------------------------------------------------------------------
# 2. Adding a category via the sidebar form
# ---------------------------------------------------------------------------


def test_category_add(seeded_page: Page):
    """Typing a name and clicking submit should add a new category via OOB swap."""
    sidebar = seeded_page.locator("#category-sidebar")
    name_input = sidebar.locator("input[name='name']")
    submit_btn = sidebar.locator("button[type='submit']", has_text="추가")

    name_input.fill("교육")
    submit_btn.click()

    # Sidebar should be refreshed (OOB swap replaces #category-sidebar)
    seeded_page.wait_for_selector("#category-sidebar")
    new_sidebar = seeded_page.locator("#category-sidebar")

    # New category should appear in the sortable list
    education_item = new_sidebar.locator("[data-category-list] li", has_text="교육")
    expect(education_item).to_be_visible()

    # "전체" count should still be 6 (no new channels added)
    all_link = new_sidebar.locator("a", has_text="전체")
    all_count = all_link.locator("span.rounded-full")
    expect(all_count).to_have_text("6")


# ---------------------------------------------------------------------------
# 3. Deleting a category (with hx-confirm dialog)
# ---------------------------------------------------------------------------


def test_category_delete(seeded_page: Page):
    """Deleting a custom category should remove it after confirm dialog."""
    sidebar = seeded_page.locator("#category-sidebar")
    custom_list = sidebar.locator("[data-category-list]")

    # Before: 2 custom categories
    expect(custom_list.locator("li[data-category-id]")).to_have_count(2)

    # Find the "투자" item's delete button (rose-500 colored button with hx-delete)
    invest_item = custom_list.locator("li", has_text="투자")

    # Accept the upcoming confirm dialog
    seeded_page.on("dialog", lambda dialog: dialog.accept())

    delete_btn = invest_item.locator("button[hx-delete]")
    # Force hover to make the button visible (it's hidden behind opacity-0 group-hover)
    invest_item.hover()
    delete_btn.click()

    # Wait for sidebar to be refreshed -- the sidebar gets fully replaced
    seeded_page.wait_for_function(
        "() => !document.querySelector('#category-sidebar [data-category-list] li a[href*=\"category_id=\"]')?.textContent?.includes('투자')",
        timeout=5000,
    )

    new_sidebar = seeded_page.locator("#category-sidebar")
    new_list = new_sidebar.locator("[data-category-list]")

    # After: only 1 custom category remains
    expect(new_list.locator("li[data-category-id]")).to_have_count(1)

    # "투자" should be gone
    expect(new_list.locator("li", has_text="투자")).to_have_count(0)

    # "미분류" should now have 4 channels (2 original + 2 moved from deleted)
    default_link = new_sidebar.locator("a[href*='category_id=']", has_text="미분류")
    default_parent = default_link.locator("xpath=..")
    default_count = default_parent.locator("span.rounded-full")
    expect(default_count).to_have_text("4")


# ---------------------------------------------------------------------------
# 4. Renaming a category via window.prompt + API call
# ---------------------------------------------------------------------------


def test_category_rename(seeded_page: Page):
    """Clicking rename trigger opens a prompt; after submit the sidebar updates."""
    sidebar = seeded_page.locator("#category-sidebar")
    custom_list = sidebar.locator("[data-category-list]")
    invest_item = custom_list.locator("li", has_text="투자")

    # Intercept the window.prompt to return a new name
    seeded_page.evaluate("window.__promptOverride = '주식'")
    seeded_page.evaluate("""
        window._origPrompt = window.prompt;
        window.prompt = function(title, defaultVal) {
            return window.__promptOverride;
        };
    """)

    # Hover to reveal buttons, then click rename trigger
    invest_item.hover()
    rename_btn = invest_item.locator("[data-category-rename-trigger]")
    rename_btn.click()

    # Wait for the sidebar to refresh via HTMX ajax
    seeded_page.wait_for_function(
        "() => document.querySelector('#category-sidebar [data-category-list]')?.textContent?.includes('주식')",
        timeout=5000,
    )

    new_sidebar = seeded_page.locator("#category-sidebar")
    renamed_item = new_sidebar.locator("[data-category-list] li", has_text="주식")
    expect(renamed_item).to_be_visible()

    # Old name should be gone
    expect(new_sidebar.locator("[data-category-list] li", has_text="투자")).to_have_count(0)

    # Restore prompt
    seeded_page.evaluate("window.prompt = window._origPrompt")


# ---------------------------------------------------------------------------
# 6. Drag reorder calls /api/categories/reorder
# ---------------------------------------------------------------------------


def test_category_drag_reorder(seeded_page: Page):
    """Dragging a category should call /api/categories/reorder with ordered_ids."""
    sidebar = seeded_page.locator("#category-sidebar")
    custom_list = sidebar.locator("[data-category-list]")

    items = custom_list.locator("li[data-category-id]")
    expect(items).to_have_count(2)

    first_id = items.nth(0).get_attribute("data-category-id")
    second_id = items.nth(1).get_attribute("data-category-id")
    first_name = items.nth(0).locator("a").inner_text().strip()
    second_name = items.nth(1).locator("a").inner_text().strip()

    # Intercept the reorder API call to verify the payload
    reorder_requests: list[dict] = []

    def capture_reorder(route):
        body = route.request.post_data_json
        reorder_requests.append(body)
        route.fallback()  # let the real request proceed

    seeded_page.route("**/api/categories/reorder", capture_reorder)

    # Perform real drag interaction via the draggable handles.
    default_cat_id = sidebar.get_attribute("data-default-category-id")
    source_handle = items.nth(1).locator("[data-drag-handle]")
    target_item = items.nth(0)
    with seeded_page.expect_request("**/api/categories/reorder"):
        source_handle.drag_to(target_item)

    # Allow route hook to append captured payload
    seeded_page.wait_for_timeout(200)
    assert len(reorder_requests) >= 1, "Expected at least one reorder request"

    payload = reorder_requests[0]
    assert "ordered_ids" in payload
    assert payload["ordered_ids"] == [
        int(default_cat_id),
        int(second_id),
        int(first_id),
    ]

    seeded_page.unroute("**/api/categories/reorder")

    # Reload the page to verify the new persisted order
    seeded_page.reload()
    seeded_page.wait_for_selector("#category-sidebar")
    new_list = seeded_page.locator("#category-sidebar [data-category-list]")
    new_items = new_list.locator("li[data-category-id]")
    expect(new_items).to_have_count(2)

    # After reorder: second_name should now be first, first_name should be second
    reordered_first = new_items.nth(0)
    reordered_second = new_items.nth(1)
    expect(reordered_first.locator("a")).to_have_text(second_name)
    expect(reordered_second.locator("a")).to_have_text(first_name)

    # Meanwhile, custom categories DO have delete buttons
    custom_list = sidebar.locator("[data-category-list]")
    custom_items = custom_list.locator("li[data-category-id]")
    expect(custom_items).to_have_count(2)

    for i in range(2):
        item = custom_items.nth(i)
        item_delete = item.locator("button[hx-delete]")
        expect(item_delete).to_have_count(1)
