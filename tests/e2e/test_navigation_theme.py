"""E2E tests for navigation, theme toggle, i18n, and page transitions."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    seed_app_setting,
    seed_channel,
    seed_video,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _seed_data(e2e_server: dict) -> None:
    """Seed minimal data: 1 channel, 1 video, language=ko."""
    db = e2e_server["db_path"]
    seed_channel(db, "UC_TEST_NAV", "TestNavChannel")
    seed_video(
        db,
        "vid_nav_001",
        "UC_TEST_NAV",
        "Navigation Test Video",
        pipeline_status="transcript_pending",
    )
    seed_app_setting(db, "language", "ko")


def _url(page: Page, path: str = "/") -> str:
    return f"{page._e2e_base_url}{path}"


# ---------------------------------------------------------------------------
# 3. test_theme_toggle_button
# ---------------------------------------------------------------------------


def test_theme_toggle_persists_and_applies_dark_css(e2e_page: Page) -> None:
    """Theme toggle, persistence, and computed dark styles work together."""
    page = e2e_page
    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    toggle_btn = page.locator("button[data-theme-toggle]")
    expect(toggle_btn).to_be_visible()

    initial_theme = page.evaluate("document.documentElement.dataset.theme")
    toggle_btn.click()
    new_theme = page.evaluate("document.documentElement.dataset.theme")
    assert new_theme != initial_theme, (
        f"Theme should change after toggle. Was '{initial_theme}', still '{new_theme}'"
    )
    assert new_theme in ("light", "dark")

    label = page.locator("span[data-theme-toggle-label]")
    if new_theme == "dark":
        # If we're now in dark, label should show the "to light" text.
        label_text = label.text_content() or ""
        assert label_text.strip() != ""

    toggle_btn.click()
    reverted_theme = page.evaluate("document.documentElement.dataset.theme")
    assert reverted_theme == initial_theme

    page.evaluate("""
        localStorage.setItem('brieftube.theme.mode', 'dark');
        localStorage.setItem('brieftube.theme.tone', 'neutral');
    """)
    page.goto(_url(page, "/channels"))
    page.wait_for_load_state("domcontentloaded")
    theme = page.evaluate("document.documentElement.dataset.theme")
    assert theme == "dark", f"Expected 'dark' theme after navigation, got '{theme}'"

    theme_mode = page.evaluate("document.documentElement.dataset.themeMode")
    assert theme_mode == "dark", f"Expected 'dark' themeMode, got '{theme_mode}'"

    page.goto(_url(page, "/settings"))
    page.wait_for_load_state("domcontentloaded")
    theme = page.evaluate("document.documentElement.dataset.theme")
    assert theme == "dark", f"Expected 'dark' theme persisted to settings page, got '{theme}'"
    app_bg = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--app-bg').trim()"
    )
    assert app_bg == "#171717", f"Expected --app-bg '#171717' in dark/neutral, got '{app_bg}'"
    body_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert body_bg != "rgb(255, 255, 255)", (
        f"Body background should not be white in dark mode, got '{body_bg}'"
    )
    color_scheme = page.evaluate("getComputedStyle(document.documentElement).colorScheme")
    assert "dark" in color_scheme, f"Expected color-scheme to contain 'dark', got '{color_scheme}'"
    page.evaluate("localStorage.setItem('brieftube.theme.mode', 'system')")


# ---------------------------------------------------------------------------
# 8. test_page_transition_fade
# ---------------------------------------------------------------------------


def test_responsive_nav_preserves_page_transition(e2e_page: Page) -> None:
    """Responsive navigation remains usable and applies the page transition."""
    page = e2e_page
    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    page.wait_for_function("!!window.BrieftubeNavTransition")
    shell = page.locator("[data-page-shell]")
    expect(shell).to_be_visible()
    page.wait_for_function(
        "document.querySelector('[data-page-shell]').classList.contains('is-visible')",
        timeout=5000,
    )
    page.set_viewport_size({"width": 1280, "height": 720})
    for href in ["/", "/channels", "/settings", "/downloads", "/queue", "/retention"]:
        link = page.locator(f'header nav a[href="{href}"]')
        expect(link).to_be_visible()

    theme_toggle = page.locator("button[data-theme-toggle]")
    expect(theme_toggle).to_be_visible()

    page.set_viewport_size({"width": 375, "height": 667})
    expect(page.locator("header")).to_be_visible()
    nav = page.locator("header nav")
    expect(nav).to_be_attached()
    brand_link = page.locator("header > div > a[href='/']")
    expect(brand_link).to_be_visible()
    channels_link = page.locator('header nav a[href="/channels"]')
    expect(channels_link).to_be_visible()
    page.evaluate("sessionStorage.setItem('brieftube.enableNextPageFade', '1')")
    channels_link.click()
    page.wait_for_url("**/channels*")
    assert urlparse(page.url).path == "/channels"
    transition_attr = page.evaluate("document.documentElement.dataset.pageTransition")
    assert transition_attr == "1", (
        f"Expected data-page-transition='1' after fade-enabled navigation, got '{transition_attr}'"
    )
