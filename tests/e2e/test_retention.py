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


def test_retention_selection_and_delete_modal(e2e_page: Page) -> None:
    """Selection and delete-all modal controls work in one browser flow."""
    _goto_retention(e2e_page)

    toggle = e2e_page.locator("[data-retention-select-toggle]")
    items = e2e_page.locator("[data-retention-select-item]")

    for i in range(5):
        expect(items.nth(i)).not_to_be_checked()
    toggle.click()
    for i in range(5):
        expect(items.nth(i)).to_be_checked()

    toggle.click()
    for i in range(5):
        expect(items.nth(i)).not_to_be_checked()

    modal = e2e_page.locator("#delete-all-modal")
    open_btn = e2e_page.locator("#open-delete-all-modal")
    confirm_cb = e2e_page.locator("#confirm-delete-all")
    submit_btn = e2e_page.locator("#submit-delete-all")

    expect(modal).to_have_class(re.compile(r"\bhidden\b"))
    open_btn.click()
    expect(modal).not_to_have_class(re.compile(r"\bhidden\b"))
    expect(modal).to_have_class(re.compile(r"\bflex\b"))
    expect(confirm_cb).not_to_be_checked()
    expect(submit_btn).to_be_disabled()
    e2e_page.keyboard.press("Escape")
    expect(modal).to_have_class(re.compile(r"\bhidden\b"))
    open_btn.click()
    expect(modal).not_to_have_class(re.compile(r"\bhidden\b"))
    modal.click(position={"x": 5, "y": 5})
    expect(modal).to_have_class(re.compile(r"\bhidden\b"))
