from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
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
