from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    disable_all_workers,
    seed_app_setting,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_server(e2e_server: dict) -> dict:
    """Seed the e2e_server DB with settings-page-specific data."""
    db = e2e_server["db_path"]

    # Language & timezone
    seed_app_setting(db, "language", "ko")
    seed_app_setting(db, "timezone", "Asia/Seoul")

    # Transcript guard: healthy defaults
    seed_app_setting(db, "transcript_guard_breaker_state", "closed")
    seed_app_setting(db, "transcript_guard_adaptive_factor", "1.0")

    # All workers disabled
    disable_all_workers(db)

    return e2e_server


@pytest.fixture()
def page(seeded_server: dict, context) -> Page:
    """Provide a Playwright page connected to the seeded server."""
    pg = context.new_page()
    pg.set_default_timeout(10_000)
    pg.set_default_navigation_timeout(15_000)
    pg._e2e_base_url = seeded_server["base_url"]
    pg._e2e_db_path = seeded_server["db_path"]
    pg._e2e_server = seeded_server
    yield pg
    pg.close()


def _goto_settings(page: Page) -> None:
    page.goto(f"{page._e2e_base_url}/settings")
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# 4. test_settings_theme_mode_toggle
# ---------------------------------------------------------------------------


def test_settings_theme_mode_toggle(page: Page) -> None:
    """Theme mode select (light/dark/system) changes data-theme on <html>."""
    _goto_settings(page)

    mode_select = page.locator("[data-theme-mode-select]")
    html = page.locator("html")

    # Switch to dark
    mode_select.select_option("dark")
    expect(html).to_have_attribute("data-theme", "dark")
    expect(html).to_have_attribute("data-theme-mode", "dark")

    # Switch to light
    mode_select.select_option("light")
    expect(html).to_have_attribute("data-theme", "light")
    expect(html).to_have_attribute("data-theme-mode", "light")

    # Switch to system — effective theme depends on OS preference,
    # just verify data-theme-mode is set to "system"
    mode_select.select_option("system")
    expect(html).to_have_attribute("data-theme-mode", "system")


# ---------------------------------------------------------------------------
# 5. test_settings_theme_tone_toggle
# ---------------------------------------------------------------------------


def test_settings_theme_tone_toggle(page: Page) -> None:
    """Theme tone select (brand/neutral/high-contrast) changes data-tone."""
    _goto_settings(page)

    tone_select = page.locator("[data-theme-tone-select]")
    html = page.locator("html")

    # Switch to brand
    tone_select.select_option("brand")
    expect(html).to_have_attribute("data-tone", "brand")

    # Switch to high-contrast
    tone_select.select_option("high-contrast")
    expect(html).to_have_attribute("data-tone", "high-contrast")

    # Switch back to neutral
    tone_select.select_option("neutral")
    expect(html).to_have_attribute("data-tone", "neutral")


# ---------------------------------------------------------------------------
# 11. test_settings_transcript_guard_reset
# ---------------------------------------------------------------------------


def test_settings_transcript_guard_reset(page: Page) -> None:
    """Guard reset: open modal, check confirm, submit, redirect with success."""
    _goto_settings(page)

    # Open the guard reset modal
    page.locator("#open-guard-reset-modal").click()

    modal = page.locator("#guard-reset-modal")
    expect(modal).not_to_have_class("hidden")

    # Submit button should be disabled initially
    submit_btn = page.locator("#submit-guard-reset")
    expect(submit_btn).to_be_disabled()

    # Check the confirmation checkbox
    confirm_checkbox = page.locator("#confirm-guard-reset")
    confirm_checkbox.check()

    # Submit button should now be enabled
    expect(submit_btn).to_be_enabled()

    # Submit the form — this POSTs and redirects to /settings?guard_reset=1
    submit_btn.click()
    page.wait_for_load_state("networkidle")

    # After redirect, the success message should be visible
    expect(page).to_have_url(f"{page._e2e_base_url}/settings?guard_reset=1")

    # The guard_reset_done banner should appear
    success_banner = page.locator("text=자막 수집 보호 상태가 초기화되었습니다")
    expect(success_banner).to_be_visible(timeout=5_000)


# ---------------------------------------------------------------------------
# 13. test_settings_channel_manage_link
# ---------------------------------------------------------------------------


def test_settings_llm_prompt_modal(page: Page) -> None:
    """LLM prompt preview opens a large modal editor and closes cleanly."""
    _goto_settings(page)

    open_button = page.locator("[data-open-llm-prompt-modal]")
    expect(open_button).to_be_visible()

    open_button.click()

    modal = page.locator("#llm-prompt-modal")
    expect(modal).to_be_visible()
    expect(page.locator("textarea[data-llm-prompt-editor]")).to_be_visible()

    page.locator("[data-close-llm-prompt-modal]").click()
    expect(modal).to_be_hidden()


# ---------------------------------------------------------------------------
# 14. test_settings_saved_toast
# ---------------------------------------------------------------------------


def test_settings_saved_toast(page: Page) -> None:
    """Toast with the save confirmation message appears after a setting change."""
    _goto_settings(page)

    # Make a timezone change to trigger a toast
    tz_select = page.locator("select[name='timezone']")
    tz_select.select_option("America/Los_Angeles")

    # Wait for the toast stack to appear
    toast_stack = page.locator("#ui-toast-stack")
    expect(toast_stack).to_be_visible(timeout=5_000)

    # At least one toast should be present
    toast_items = toast_stack.locator("div")
    expect(toast_items.first).to_be_visible()

    # The toast should contain the emerald (success) styling
    expect(toast_items.first).to_have_class(re.compile(r"border-emerald-300"))

    # Restore
    tz_select.select_option("Asia/Seoul")
    page.wait_for_timeout(500)
