from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    get_db_connection,
    make_past_time,
    seed_app_setting,
    seed_channel,
    seed_video,
)

CHANNEL_ID = "UC_retention_test"
CHANNEL_NAME = "RetentionTestChannel"

EXPIRED_IDS = [f"EXP_VID_{i}" for i in range(1, 6)]
NON_EXPIRED_IDS = [f"FRESH_VID_{i}" for i in range(1, 4)]


@pytest.fixture(autouse=True)
def _seed_retention_data(e2e_server: dict) -> None:
    """Seed retention_days setting, 1 channel, 5 expired + 3 non-expired videos."""
    db = e2e_server["db_path"]

    seed_app_setting(db, "retention_days", "30")
    seed_channel(db, CHANNEL_ID, CHANNEL_NAME)

    expired_time = make_past_time(40)
    for vid in EXPIRED_IDS:
        seed_video(
            db,
            vid,
            CHANNEL_ID,
            f"Expired Video {vid}",
            upload_time=expired_time,
            pipeline_status="done",
        )

    fresh_time = make_past_time(10)
    for vid in NON_EXPIRED_IDS:
        seed_video(
            db,
            vid,
            CHANNEL_ID,
            f"Fresh Video {vid}",
            upload_time=fresh_time,
            pipeline_status="done",
        )


def _goto_retention(page: Page) -> None:
    page.goto(f"{page._e2e_base_url}/retention")


# ---------- 1. Page loads ----------


def test_retention_page_loads(e2e_page: Page) -> None:
    """/retention loads, retention_days displayed, expired table visible."""
    _goto_retention(e2e_page)

    # Page title section contains retention days value "30"
    e2e_page.wait_for_selector("text=30")

    # The expired video table should be visible
    table = e2e_page.locator("table")
    expect(table).to_be_visible()

    # The form with data-retention-form should exist
    form = e2e_page.locator("[data-retention-form]")
    expect(form).to_be_visible()


# ---------- 2. Expired count ----------


def test_retention_expired_count(e2e_page: Page) -> None:
    """5 expired shown, 3 non-expired excluded."""
    _goto_retention(e2e_page)

    # The expired count label should show 5
    count_label = e2e_page.locator("span.font-semibold", has_text="5")
    expect(count_label).to_be_visible()

    # Each expired video row has a checkbox with data-retention-select-item
    checkboxes = e2e_page.locator("[data-retention-select-item]")
    expect(checkboxes).to_have_count(5)

    # Non-expired video IDs should NOT appear on the page
    for vid in NON_EXPIRED_IDS:
        expect(e2e_page.locator(f"text={vid}")).to_have_count(0)

    # Expired video IDs should appear (use .first to handle multiple matches)
    for vid in EXPIRED_IDS:
        expect(e2e_page.locator(f"text={vid}").first).to_be_visible()


# ---------- 3. Select toggle ----------


def test_retention_select_toggle(e2e_page: Page) -> None:
    """Select all toggle checks/unchecks all checkboxes."""
    _goto_retention(e2e_page)

    toggle = e2e_page.locator("[data-retention-select-toggle]")
    items = e2e_page.locator("[data-retention-select-item]")

    # Initially none are checked
    for i in range(5):
        expect(items.nth(i)).not_to_be_checked()

    # Click toggle → all checked
    toggle.click()
    for i in range(5):
        expect(items.nth(i)).to_be_checked()

    # Click toggle again → all unchecked
    toggle.click()
    for i in range(5):
        expect(items.nth(i)).not_to_be_checked()


# ---------- 4. Delete selected ----------


def test_retention_delete_selected(e2e_page: Page) -> None:
    """Select 2 → delete → redirect → ?deleted=2 shown."""
    _goto_retention(e2e_page)

    items = e2e_page.locator("[data-retention-select-item]")
    delete_btn = e2e_page.locator("[data-retention-delete-button]")

    # Delete button should be disabled initially
    expect(delete_btn).to_be_disabled()

    # Check the first 2 checkboxes
    items.nth(0).check()
    items.nth(1).check()

    # Delete button should now be enabled
    expect(delete_btn).to_be_enabled()

    # Click delete → form submits → HTMX swaps retention content
    delete_btn.click()

    # Wait for the content to refresh
    e2e_page.wait_for_selector("#retention-content", state="visible")

    # The success message should appear
    success_msg = e2e_page.locator(".bg-emerald-50")
    expect(success_msg).to_be_visible()
    expect(success_msg).to_contain_text("2")


# ---------- 5. Delete all modal: initial state ----------


def test_retention_delete_all_modal(e2e_page: Page) -> None:
    """Modal open → confirm checkbox unchecked → submit disabled."""
    _goto_retention(e2e_page)

    modal = e2e_page.locator("#delete-all-modal")
    open_btn = e2e_page.locator("#open-delete-all-modal")
    confirm_cb = e2e_page.locator("#confirm-delete-all")
    submit_btn = e2e_page.locator("#submit-delete-all")

    # Modal should start hidden
    expect(modal).to_have_class(re.compile(r"\bhidden\b"))

    # Open modal
    open_btn.click()

    # Modal should be visible (has flex, no hidden)
    expect(modal).not_to_have_class(re.compile(r"\bhidden\b"))
    expect(modal).to_have_class(re.compile(r"\bflex\b"))

    # Confirm checkbox should be unchecked
    expect(confirm_cb).not_to_be_checked()

    # Submit button should be disabled
    expect(submit_btn).to_be_disabled()


# ---------- 6. Delete all confirm ----------


def test_retention_delete_all_confirm(e2e_page: Page) -> None:
    """Confirm check → submit → all expired deleted."""
    _goto_retention(e2e_page)

    open_btn = e2e_page.locator("#open-delete-all-modal")
    confirm_cb = e2e_page.locator("#confirm-delete-all")
    submit_btn = e2e_page.locator("#submit-delete-all")

    # Open modal
    open_btn.click()

    # Check confirmation checkbox
    confirm_cb.check()

    # Submit button should now be enabled
    expect(submit_btn).to_be_enabled()

    # Submit the form
    submit_btn.click()

    # Wait for the content to refresh
    e2e_page.wait_for_selector("#retention-content", state="visible")

    # The success message should appear showing some deletion count
    success_msg = e2e_page.locator(".bg-emerald-50")
    expect(success_msg).to_be_visible()

    # The expired table should now be gone (empty state or reduced rows)
    # Since all expired videos were deleted, we should see the empty message
    empty_msg = e2e_page.locator("text=보관 만료된 영상이 없습니다.")
    expect(empty_msg).to_be_visible()

    # Non-expired videos must remain after expired-only delete.
    conn = get_db_connection(e2e_page._e2e_db_path)
    try:
        placeholders = ",".join("?" for _ in NON_EXPIRED_IDS)
        row = conn.execute(
            f"SELECT COUNT(1) AS cnt FROM videos WHERE video_id IN ({placeholders})",
            tuple(NON_EXPIRED_IDS),
        ).fetchone()
        assert int(row["cnt"]) == len(NON_EXPIRED_IDS)
    finally:
        conn.close()


# ---------- 7. Delete all modal dismiss ----------


def test_retention_delete_all_modal_dismiss(e2e_page: Page) -> None:
    """Escape and backdrop click close the modal."""
    _goto_retention(e2e_page)

    modal = e2e_page.locator("#delete-all-modal")
    open_btn = e2e_page.locator("#open-delete-all-modal")

    # --- Escape key ---
    open_btn.click()
    expect(modal).not_to_have_class(re.compile(r"\bhidden\b"))

    e2e_page.keyboard.press("Escape")
    expect(modal).to_have_class(re.compile(r"\bhidden\b"))

    # --- Backdrop click ---
    open_btn.click()
    expect(modal).not_to_have_class(re.compile(r"\bhidden\b"))

    # Click the modal backdrop (the outer div itself, not the inner card)
    # Use position to click top-left corner which is the backdrop area
    modal.click(position={"x": 5, "y": 5})
    expect(modal).to_have_class(re.compile(r"\bhidden\b"))


# ---------- 8. Empty state ----------


def test_retention_empty_state(e2e_page: Page) -> None:
    """No expired videos → empty message shown."""
    db = e2e_page._e2e_db_path

    # Set retention_days very high so nothing is expired
    seed_app_setting(db, "retention_days", "9999")

    _goto_retention(e2e_page)

    # The empty message should be visible
    empty_msg = e2e_page.locator("text=보관 만료된 영상이 없습니다.")
    expect(empty_msg).to_be_visible()

    # The table should not exist
    expect(e2e_page.locator("table")).to_have_count(0)

    # The delete-all button should not exist
    expect(e2e_page.locator("#open-delete-all-modal")).to_have_count(0)

    # The modal should not exist
    expect(e2e_page.locator("#delete-all-modal")).to_have_count(0)

    # Restore for other tests
    seed_app_setting(db, "retention_days", "30")
