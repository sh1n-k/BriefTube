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
# 1. test_settings_page_loads
# ---------------------------------------------------------------------------

def test_settings_page_loads(page: Page) -> None:
    """Settings page loads and core setting sections are visible."""
    _goto_settings(page)

    # Page title
    expect(page).to_have_title("BriefTube - 설정")

    # Language section
    expect(page.locator("select[name='language']")).to_be_visible()

    # Timezone section
    expect(page.locator("select[name='timezone']")).to_be_visible()

    # Theme section (data-theme-settings container)
    expect(page.locator("[data-theme-settings]")).to_be_visible()

    # Workers section
    expect(page.locator("input[name='rss'][type='checkbox']")).to_be_visible()

    # Videos per page
    expect(page.locator("input[name='videos_per_page']")).to_be_visible()

    # Policy section
    expect(page.locator("input[name='rss_bootstrap_lookback_days']")).to_be_visible()
    expect(page.locator("input[name='retention_days']")).to_be_visible()

    # Download settings
    expect(page.locator("select[name='download_quality']")).to_be_visible()

    # Transcript guard danger zone
    expect(page.locator("#open-guard-reset-modal")).to_be_visible()

    # LLM runtime status
    expect(page.locator("#llm-runtime-status")).to_be_visible()


# ---------------------------------------------------------------------------
# 2. test_settings_language_change
# ---------------------------------------------------------------------------

def test_settings_language_change(page: Page) -> None:
    """Changing the language dropdown fires HTMX PUT and shows toast."""
    _goto_settings(page)

    lang_select = page.locator("select[name='language']")
    expect(lang_select).to_have_value("ko")

    # Switch to English
    lang_select.select_option("en")

    # Toast should appear with the save message
    toast = page.locator("#ui-toast-stack div").first
    expect(toast).to_be_visible(timeout=5_000)

    # Restore to Korean for subsequent tests
    page.goto(f"{page._e2e_base_url}/settings")
    page.wait_for_load_state("networkidle")
    page.locator("select[name='language']").select_option("ko")
    expect(page.locator("#ui-toast-stack div").first).to_be_visible(timeout=5_000)


# ---------------------------------------------------------------------------
# 3. test_settings_timezone_change
# ---------------------------------------------------------------------------

def test_settings_timezone_change(page: Page) -> None:
    """Changing timezone fires API call and shows toast."""
    _goto_settings(page)

    tz_select = page.locator("select[name='timezone']")

    # Change to a different timezone
    tz_select.select_option("America/New_York")

    toast = page.locator("#ui-toast-stack div").first
    expect(toast).to_be_visible(timeout=5_000)

    # Restore original
    tz_select.select_option("Asia/Seoul")
    page.wait_for_timeout(500)


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
# 6. test_settings_workers_toggle
# ---------------------------------------------------------------------------

def test_settings_workers_toggle(page: Page) -> None:
    """Toggling a worker checkbox fires HTMX PUT and shows toast."""
    _goto_settings(page)

    rss_checkbox = page.locator("input[name='rss'][type='checkbox']")

    # All workers start disabled
    expect(rss_checkbox).not_to_be_checked()

    # Enable RSS worker
    rss_checkbox.check()

    toast = page.locator("#ui-toast-stack div").first
    expect(toast).to_be_visible(timeout=5_000)

    # Disable it again
    page.wait_for_timeout(500)
    rss_checkbox.uncheck()
    page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# 7. test_settings_videos_per_page
# ---------------------------------------------------------------------------

def test_settings_videos_per_page(page: Page) -> None:
    """Changing videos_per_page input triggers a save via HTMX."""
    _goto_settings(page)

    vpp_input = page.locator("input[name='videos_per_page']")

    # Clear and type a new value
    vpp_input.fill("25")

    # The form has hx-trigger with `input changed delay:1s`, so wait
    toast = page.locator("#ui-toast-stack div").first
    expect(toast).to_be_visible(timeout=5_000)


# ---------------------------------------------------------------------------
# 8. test_settings_policy_lookback_days
# ---------------------------------------------------------------------------

def test_settings_policy_lookback_days(page: Page) -> None:
    """Changing RSS lookback days triggers a policy save."""
    _goto_settings(page)

    lookback_input = page.locator("input[name='rss_bootstrap_lookback_days']")
    lookback_input.fill("14")

    # Wait for the delayed HTMX trigger
    toast = page.locator("#ui-toast-stack div").first
    expect(toast).to_be_visible(timeout=5_000)


# ---------------------------------------------------------------------------
# 9. test_settings_policy_retention_days
# ---------------------------------------------------------------------------

def test_settings_policy_retention_days(page: Page) -> None:
    """Changing retention days triggers a policy save."""
    _goto_settings(page)

    retention_input = page.locator("input[name='retention_days']")
    retention_input.fill("60")

    toast = page.locator("#ui-toast-stack div").first
    expect(toast).to_be_visible(timeout=5_000)


# ---------------------------------------------------------------------------
# 10. test_settings_download_quality
# ---------------------------------------------------------------------------

def test_settings_download_quality(page: Page) -> None:
    """Changing download quality fires save and shows toast."""
    _goto_settings(page)

    quality_select = page.locator("select[name='download_quality']")
    quality_select.select_option("720")

    toast = page.locator("#ui-toast-stack div").first
    expect(toast).to_be_visible(timeout=5_000)


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
# 12. test_settings_llm_runtime_status
# ---------------------------------------------------------------------------

def test_settings_llm_runtime_status(page: Page) -> None:
    """LLM runtime status section is visible with status info."""
    _goto_settings(page)

    status_section = page.locator("#llm-runtime-status")
    expect(status_section).to_be_visible()

    # The section should have the data attribute
    expect(status_section).to_have_attribute("data-llm-runtime-status", "")

    # Should contain the refresh button
    refresh_btn = status_section.locator("button[hx-get='/views/settings/llm/runtime-status']")
    expect(refresh_btn).to_be_visible()

    # Should contain the resume button
    resume_btn = status_section.locator("button[hx-post='/views/settings/llm/resume']")
    expect(resume_btn).to_be_visible()


# ---------------------------------------------------------------------------
# 13. test_settings_channel_manage_link
# ---------------------------------------------------------------------------

def test_settings_channel_manage_link(page: Page) -> None:
    """The channel manage link navigates to /channels."""
    _goto_settings(page)

    channel_link = page.locator("a[href='/channels']").first
    expect(channel_link).to_be_visible()

    channel_link.click()
    page.wait_for_load_state("networkidle")

    expect(page).to_have_url(f"{page._e2e_base_url}/channels")


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
