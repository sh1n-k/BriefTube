from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.dialog_helpers import (
    AppDialogRecorder,
    cancel_app_dialog,
    expect_app_dialog_open,
)
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

    expect_app_dialog_open(page, text="재활성화")
    cancel_app_dialog(page)


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


# ---------------------------------------------------------------------------
# Channel delete guards: named confirm, repeat-click guard, scroll restore
# ---------------------------------------------------------------------------


class _NativeDialogRecorder:
    """Only for the safety-net test where the app modal handler is deliberately blocked."""

    def __init__(self, page: Page) -> None:
        self.messages: list[str] = []
        page.on("dialog", self._handle)

    def _handle(self, dialog) -> None:
        self.messages.append(dialog.message)
        dialog.dismiss()


_DialogRecorder = AppDialogRecorder


def _record_delete_posts(page: Page) -> list[str]:
    posts: list[str] = []

    def _on_request(request) -> None:
        if request.method != "POST":
            return
        url = request.url
        if "/views/channels/" not in url:
            return
        if "/delete" in url or "bulk_action=delete" in (request.post_data or ""):
            posts.append(url)

    page.on("request", _on_request)
    return posts


def _row(page: Page, channel_id: str):
    return page.locator(f"[data-channel-meta-item]:has(input[value='{channel_id}'])")


def _delete_button(page: Page, channel_id: str):
    return page.locator(f"button[hx-post^='/views/channels/{channel_id}/delete']")


def _seed_scroll_channels(db_path: str, *, count: int = 20, is_active: int = 1) -> list[str]:
    prefix = "UC_SCROLL_A" if is_active else "UC_SCROLL_I"
    ids = [f"{prefix}{index:02d}" for index in range(count)]
    for index, channel_id in enumerate(ids):
        seed_channel(db_path, channel_id, f"스크롤채널{index:02d}", is_active=is_active)
    return ids


def _scroll_top(page: Page) -> float:
    return float(page.locator("[data-channel-scroll]").evaluate("(node) => node.scrollTop"))


def _set_scroll_top(page: Page, top: int) -> float:
    scroller = page.locator("[data-channel-scroll]")
    scroller.evaluate("(node, top) => { node.scrollTop = top; }", top)
    return _scroll_top(page)


@pytest.mark.e2e
def test_channel_delete_requires_named_confirm(page: Page) -> None:
    """Single delete shows the channel name; dismiss keeps it, accept removes it."""
    dialogs = _DialogRecorder(page)
    posts = _record_delete_posts(page)
    _goto_channels(page)

    _delete_button(page, "UC_TECH_01").click()
    page.wait_for_timeout(300)
    assert len(dialogs.messages) == 1
    assert "'기술채널A' 채널을 제거합니다." in dialogs.messages[0]
    assert "영구 삭제" in dialogs.messages[0]
    assert posts == []
    expect(_row(page, "UC_TECH_01")).to_have_count(1)

    dialogs.accept = True
    with page.expect_response(lambda r: "/views/channels/UC_TECH_01/delete" in r.url):
        _delete_button(page, "UC_TECH_01").click()
    expect(_row(page, "UC_TECH_01")).to_have_count(0)
    expect(_row(page, "UC_TECH_02")).to_have_count(1)
    assert len(posts) == 1


@pytest.mark.e2e
def test_channel_bulk_delete_confirm_lists_selected_names(page: Page) -> None:
    """Active bulk delete lists the selected names before deleting."""
    dialogs = _DialogRecorder(page)
    posts = _record_delete_posts(page)
    _goto_channels(page)

    page.locator("input[data-channel-select-item][value='UC_INVEST_01']").check()
    page.locator("input[data-channel-select-item][value='UC_TECH_02']").check()
    bulk_button = page.locator("button[data-channel-bulk-submit]")

    bulk_button.click()
    page.wait_for_timeout(300)
    assert len(dialogs.messages) == 1
    message = dialogs.messages[0]
    assert "선택한 채널 2개를 제거합니다." in message
    assert "- 투자채널A" in message
    assert "- 기술채널B" in message
    assert posts == []
    expect(_row(page, "UC_INVEST_01")).to_have_count(1)

    dialogs.accept = True
    with page.expect_response(lambda r: "/views/channels/delete-selected" in r.url):
        bulk_button.click()
    expect(_row(page, "UC_INVEST_01")).to_have_count(0)
    expect(_row(page, "UC_TECH_02")).to_have_count(0)
    expect(_row(page, "UC_INVEST_02")).to_have_count(1)
    assert len(posts) == 1


@pytest.mark.e2e
def test_inactive_bulk_delete_confirms_but_reactivate_keeps_modal(page: Page) -> None:
    """Inactive bulk delete and reactivate each ask once in the app modal before any POST."""
    dialogs = _DialogRecorder(page)
    posts = _record_delete_posts(page)
    reactivate_posts: list[str] = []
    page.on(
        "request",
        lambda r: (
            reactivate_posts.append(r.post_data or "")
            if r.method == "POST" and "reactivate-selected" in r.url
            else None
        ),
    )
    _goto_channels(page, status="inactive")

    page.locator("input[data-channel-select-item][value='UC_INACTIVE_01']").check()
    reactivate_btn = page.locator(
        "button[data-channel-bulk-submit]:not([name='bulk_action'])"
    ).first
    reactivate_btn.click()
    page.wait_for_timeout(300)
    assert len(dialogs.messages) == 1
    assert "재활성화" in dialogs.titles[0]
    assert reactivate_posts == []

    delete_btn = page.locator(
        "button[data-channel-bulk-submit][name='bulk_action'][value='delete']"
    )
    delete_btn.click()
    page.wait_for_timeout(300)
    assert len(dialogs.messages) == 2
    assert "선택한 채널 1개를 제거합니다." in dialogs.messages[1]
    assert "- 비활성채널A" in dialogs.messages[1]
    assert posts == []
    assert reactivate_posts == []
    expect(_row(page, "UC_INACTIVE_01")).to_have_count(1)

    dialogs.accept = True
    with page.expect_response(lambda r: "reactivate-selected" in r.url):
        delete_btn.click()
    expect(_row(page, "UC_INACTIVE_01")).to_have_count(0)
    expect(_row(page, "UC_INACTIVE_02")).to_have_count(1)
    assert len(posts) == 1
    assert len(reactivate_posts) == 1
    assert "bulk_action=delete" in reactivate_posts[0]


@pytest.mark.e2e
def test_reactivate_sends_one_request_only_after_modal_confirm(page: Page) -> None:
    """Regression: reactivate used to POST before the modal and again after confirming."""
    dialogs = _DialogRecorder(page)
    reactivate_posts: list[str] = []
    page.on(
        "request",
        lambda r: (
            reactivate_posts.append(r.post_data or "")
            if r.method == "POST" and "reactivate-selected" in r.url
            else None
        ),
    )
    _goto_channels(page, status="inactive")
    # Re-render once so the form is a swapped-in fragment, like after any list refresh.
    with page.expect_response(lambda r: "/views/channel-list" in r.url):
        page.evaluate(
            "() => htmx.ajax('GET', '/views/channel-list?status=inactive',"
            " {target: '#channel-list-wrap', swap: 'innerHTML'})"
        )
    page.locator("input[data-channel-select-item][value='UC_INACTIVE_02']").check()
    reactivate_btn = page.locator(
        "button[data-channel-bulk-submit]:not([name='bulk_action'])"
    ).first

    reactivate_btn.click()
    page.wait_for_timeout(500)
    assert len(dialogs.messages) == 1
    assert reactivate_posts == []

    dialogs.accept = True
    with page.expect_response(lambda r: "reactivate-selected" in r.url):
        reactivate_btn.click()
    page.wait_for_timeout(500)
    assert len(dialogs.messages) == 2
    assert len(reactivate_posts) == 1
    assert "bulk_action" not in reactivate_posts[0]
    expect(page.locator("#channel-reactivate-blocking-overlay")).to_be_hidden()


@pytest.mark.e2e
def test_reactivate_over_limit_shows_toast_without_request(page: Page) -> None:
    dialogs = _DialogRecorder(page)
    reactivate_posts: list[str] = []
    page.on(
        "request",
        lambda r: (
            reactivate_posts.append(r.url)
            if r.method == "POST" and "reactivate-selected" in r.url
            else None
        ),
    )
    _seed_scroll_channels(page._e2e_db_path, count=50, is_active=0)
    _goto_channels(page, status="inactive")
    expect(page.locator("[data-channel-manage-form]")).to_have_attribute(
        "data-reactivate-batch-limit", "50"
    )
    page.locator("[data-channel-select-all]").check()
    page.locator("button[data-channel-bulk-submit]:not([name='bulk_action'])").first.click()
    page.wait_for_timeout(500)

    assert dialogs.messages == []
    assert reactivate_posts == []


@pytest.mark.e2e
def test_channel_delete_blocks_other_clicks_while_in_flight(page: Page) -> None:
    """While a delete is in flight its button is disabled and other deletes are dropped."""
    dialogs = _DialogRecorder(page)
    dialogs.accept = True
    posts = _record_delete_posts(page)

    def _delay_delete(route) -> None:
        page.wait_for_timeout(1500)
        route.continue_()

    page.route(re.compile(r".*/views/channels/UC_INVEST_01/delete.*"), _delay_delete)
    _goto_channels(page)

    first_button = _delete_button(page, "UC_INVEST_01")
    with page.expect_response(lambda r: "/views/channels/UC_INVEST_01/delete" in r.url):
        first_button.click()
        expect(first_button).to_be_disabled()
        _delete_button(page, "UC_INVEST_02").click()
        _delete_button(page, "UC_TECH_01").click(force=True)

    expect(_row(page, "UC_INVEST_01")).to_have_count(0)
    page.wait_for_timeout(500)
    assert len(posts) == 1
    assert len(dialogs.messages) == 1
    expect(_row(page, "UC_INVEST_02")).to_have_count(1)
    expect(_row(page, "UC_TECH_01")).to_have_count(1)


@pytest.mark.e2e
def test_channel_delete_keeps_list_scroll_position(page: Page) -> None:
    """Deleting a row keeps the list scroll position instead of jumping to the top."""
    scroll_ids = _seed_scroll_channels(page._e2e_db_path)
    dialogs = _DialogRecorder(page)
    dialogs.accept = True
    _goto_channels(page)

    before = _set_scroll_top(page, 600)
    assert before > 0
    target = page.locator("[data-channel-meta-item]").nth(12)
    target_id = target.locator("input[data-channel-select-item]").get_attribute("value")
    assert target_id is not None

    with page.expect_response(lambda r: f"/views/channels/{target_id}/delete" in r.url):
        _delete_button(page, target_id).click()
    expect(_row(page, target_id)).to_have_count(0)
    page.wait_for_timeout(200)

    after = _scroll_top(page)
    assert abs(after - before) <= 60, (before, after)
    assert len(scroll_ids) == 20


@pytest.mark.e2e
def test_channel_category_switch_resets_list_scroll(page: Page) -> None:
    """Switching category is a different list, so the scroll position is not restored."""
    _seed_scroll_channels(page._e2e_db_path)
    _goto_channels(page)
    assert _set_scroll_top(page, 600) > 0

    category_id = str(page._e2e_server["categories"]["투자"])
    with page.expect_response(lambda r: "/views/channel-list" in r.url):
        page.locator(f"#category-sidebar a[href*='category_id={category_id}']").first.click()
    expect(page.locator("[data-channel-list-fragment]")).to_have_attribute(
        "data-channel-list-refresh-url", re.compile(r"category_id=")
    )
    page.wait_for_timeout(200)
    assert _scroll_top(page) == 0


@pytest.mark.e2e
def test_channel_list_auto_refresh_keeps_scroll_position(page: Page) -> None:
    """The periodic inactive-list refresh swaps the fragment without resetting scroll."""
    _seed_scroll_channels(page._e2e_db_path, is_active=0)
    page.clock.install()
    _goto_channels(page, status="inactive")
    fragment = page.locator("[data-channel-list-fragment]")
    expect(fragment).to_have_attribute("data-channel-list-auto-refresh", "1")
    fragment.evaluate("(node) => node.setAttribute('data-e2e-stale', '1')")
    before = _set_scroll_top(page, 600)
    assert before > 0

    with page.expect_response(lambda r: "/views/channel-list?status=inactive" in r.url):
        page.clock.run_for(16_000)
    expect(page.locator("[data-channel-list-fragment][data-e2e-stale]")).to_have_count(0)
    # The response also carries an OOB RSS preview card; the list itself must survive.
    expect(page.locator("#channel-list-wrap > [data-channel-list-fragment]")).to_have_count(1)
    expect(page.locator("[data-channel-row]")).to_have_count(len(INACTIVE_CHANNELS) + 20)
    page.wait_for_timeout(200)

    assert abs(_scroll_top(page) - before) <= 1


@pytest.mark.e2e
def test_stale_auto_refresh_response_does_not_restore_deleted_channel(page: Page) -> None:
    """A poll response fetched before a delete must not bring the deleted row back."""
    dialogs = _DialogRecorder(page)
    dialogs.accept = True
    page.clock.install()

    def _hold_poll_response(route) -> None:
        response = route.fetch()
        page.wait_for_timeout(1500)
        route.fulfill(response=response)

    _goto_channels(page, status="inactive")
    page.route(re.compile(r".*/views/channel-list\?status=inactive$"), _hold_poll_response)

    with page.expect_response(lambda r: "/views/channel-list?status=inactive" in r.url) as poll:
        page.clock.run_for(16_000)
        with page.expect_response(lambda r: "/views/channels/UC_INACTIVE_01/delete" in r.url):
            _delete_button(page, "UC_INACTIVE_01").click()
        expect(_row(page, "UC_INACTIVE_01")).to_have_count(0)
    assert poll.value.ok
    page.wait_for_timeout(300)

    expect(_row(page, "UC_INACTIVE_01")).to_have_count(0)
    expect(_row(page, "UC_INACTIVE_02")).to_have_count(1)


@pytest.mark.e2e
def test_channel_bulk_delete_confirm_truncates_long_selection(page: Page) -> None:
    """Long selections list the first 10 names and summarize the rest."""
    _seed_scroll_channels(page._e2e_db_path, count=12)
    dialogs = _DialogRecorder(page)
    posts = _record_delete_posts(page)
    _goto_channels(page)

    page.locator("[data-channel-select-all]").check()
    page.locator("button[data-channel-bulk-submit]").click()
    page.wait_for_timeout(300)

    total = len(ACTIVE_CHANNELS) + 12
    assert len(dialogs.messages) == 1
    message = dialogs.messages[0]
    assert f"선택한 채널 {total}개를 제거합니다." in message
    assert message.count("\n- ") == 10
    assert f"외 {total - 10}개" in message
    assert posts == []
    expect(page.locator("[data-channel-row]")).to_have_count(total)


@pytest.mark.e2e
def test_rss_priority_change_does_not_disable_bulk_buttons(page: Page) -> None:
    """Form-level delete guards must not leak into other htmx controls in the form."""
    _goto_channels(page)
    page.locator("input[data-channel-select-item][value='UC_INVEST_01']").check()
    bulk_button = page.locator("button[data-channel-bulk-submit]")
    expect(bulk_button).to_be_enabled()

    select = page.locator("[data-channel-meta-item]:has(input[value='UC_INVEST_01']) select").first
    current = select.input_value()
    options = select.locator("option").evaluate_all("(nodes) => nodes.map((n) => n.value)")
    next_value = next(value for value in options if value != current)
    with page.expect_response(lambda r: "/rss-priority" in r.url) as patch:
        select.select_option(next_value)
    assert patch.value.ok
    expect(bulk_button).to_be_enabled()
    expect(_row(page, "UC_INVEST_01")).to_have_count(1)


@pytest.mark.e2e
@pytest.mark.allow_native_dialog
def test_active_bulk_delete_falls_back_to_static_confirm(page: Page) -> None:
    """Safety net: if no app modal handler runs, htmx still asks with the static hx-confirm."""
    dialogs = _NativeDialogRecorder(page)
    posts = _record_delete_posts(page)
    _goto_channels(page)
    page.evaluate(
        "() => document.addEventListener("
        "'htmx:confirm', (event) => event.stopImmediatePropagation(), true)"
    )

    page.locator("input[data-channel-select-item][value='UC_INVEST_01']").check()
    page.locator("button[data-channel-bulk-submit]").click()
    page.wait_for_timeout(300)

    assert len(dialogs.messages) == 1
    assert dialogs.messages[0].startswith("선택한 채널을 제거합니다.")
    assert posts == []
    expect(_row(page, "UC_INVEST_01")).to_have_count(1)


@pytest.mark.e2e
def test_inactive_row_delete_shows_its_own_confirm_with_selection(page: Page) -> None:
    """Row buttons inside the inactive form keep their own confirm even with a selection."""
    dialogs = _DialogRecorder(page)
    _goto_channels(page, status="inactive")
    page.locator("input[data-channel-select-item][value='UC_INACTIVE_01']").check()

    _delete_button(page, "UC_INACTIVE_02").click()
    page.wait_for_timeout(300)

    assert len(dialogs.messages) == 1
    assert dialogs.titles[0] == "채널 제거"
    assert "'비활성채널B' 채널을 제거합니다." in dialogs.messages[0]
    expect(page.locator("#channel-reactivate-blocking-overlay")).to_have_count(0)
    expect(_row(page, "UC_INACTIVE_02")).to_have_count(1)
