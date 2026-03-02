"""E2E tests for navigation, theme toggle, i18n, and page transitions."""
from __future__ import annotations

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
# 1. test_navigation_links
# ---------------------------------------------------------------------------

def test_navigation_links(e2e_page: Page) -> None:
    """All nav links click and navigate to the correct pages."""
    page = e2e_page
    page.goto(_url(page, "/"))

    nav_targets = {
        "/": "nav_videos",
        "/channels": "nav_channels",
        "/settings": "nav_settings",
        "/downloads": "nav_downloads",
        "/queue": "nav_queue",
        "/retention": "nav_retention",
    }

    for path in nav_targets:
        link = page.locator(f'header nav a[href="{path}"]')
        expect(link).to_be_visible()

    # Click each link and verify the resulting URL path.
    for path in ["/channels", "/settings", "/downloads", "/queue", "/retention", "/"]:
        link = page.locator(f'header nav a[href="{path}"]')
        link.click()
        page.wait_for_url(f"**{path}")
        assert page.url.endswith(path) or page.url.endswith(path + "/") or path in page.url


# ---------------------------------------------------------------------------
# 2. test_navigation_active_highlight
# ---------------------------------------------------------------------------

def test_navigation_active_highlight(e2e_page: Page) -> None:
    """Current page nav link has the active style (bg-white/20)."""
    page = e2e_page

    targets = [
        ("/", "/"),
        ("/channels", "/channels"),
        ("/settings", "/settings"),
    ]

    for nav_path, url_path in targets:
        page.goto(_url(page, url_path))
        link = page.locator(f'header nav a[href="{nav_path}"]')
        expect(link).to_be_visible()
        classes = link.get_attribute("class") or ""
        assert "bg-white/20" in classes, (
            f"Nav link '{nav_path}' should have active class 'bg-white/20' "
            f"on page '{url_path}', got: {classes}"
        )

        # Other nav links should NOT have active class.
        for other_path, _ in targets:
            if other_path == nav_path:
                continue
            other_link = page.locator(f'header nav a[href="{other_path}"]')
            other_classes = other_link.get_attribute("class") or ""
            assert "bg-white/20" not in other_classes, (
                f"Nav link '{other_path}' should NOT have active class on page '{url_path}'"
            )


# ---------------------------------------------------------------------------
# 3. test_theme_toggle_button
# ---------------------------------------------------------------------------

def test_theme_toggle_button(e2e_page: Page) -> None:
    """Theme toggle button changes data-theme attribute on <html>."""
    page = e2e_page
    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    toggle_btn = page.locator("button[data-theme-toggle]")
    expect(toggle_btn).to_be_visible()

    # Get initial theme.
    initial_theme = page.evaluate("document.documentElement.dataset.theme")

    # Click toggle.
    toggle_btn.click()
    new_theme = page.evaluate("document.documentElement.dataset.theme")
    assert new_theme != initial_theme, (
        f"Theme should change after toggle. Was '{initial_theme}', still '{new_theme}'"
    )
    assert new_theme in ("light", "dark")

    # Toggle label text should update.
    label = page.locator("span[data-theme-toggle-label]")
    if new_theme == "dark":
        # If we're now in dark, label should show the "to light" text.
        label_text = label.text_content() or ""
        assert label_text.strip() != ""

    # Click again to toggle back.
    toggle_btn.click()
    reverted_theme = page.evaluate("document.documentElement.dataset.theme")
    assert reverted_theme == initial_theme


# ---------------------------------------------------------------------------
# 4. test_theme_persists_across_pages
# ---------------------------------------------------------------------------

def test_theme_persists_across_pages(e2e_page: Page) -> None:
    """Set theme then navigate; theme is preserved via localStorage."""
    page = e2e_page
    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    # Force dark mode via localStorage, then apply.
    page.evaluate("""
        localStorage.setItem('brieftube.theme.mode', 'dark');
        localStorage.setItem('brieftube.theme.tone', 'neutral');
    """)
    # Navigate to another page.
    page.goto(_url(page, "/channels"))
    page.wait_for_load_state("domcontentloaded")

    # The inline <script> in base.html should read localStorage and apply theme.
    theme = page.evaluate("document.documentElement.dataset.theme")
    assert theme == "dark", f"Expected 'dark' theme after navigation, got '{theme}'"

    theme_mode = page.evaluate("document.documentElement.dataset.themeMode")
    assert theme_mode == "dark", f"Expected 'dark' themeMode, got '{theme_mode}'"

    # Navigate again.
    page.goto(_url(page, "/settings"))
    page.wait_for_load_state("domcontentloaded")

    theme = page.evaluate("document.documentElement.dataset.theme")
    assert theme == "dark", f"Expected 'dark' theme persisted to settings page, got '{theme}'"

    # Clean up: restore default.
    page.evaluate("localStorage.setItem('brieftube.theme.mode', 'system')")


# ---------------------------------------------------------------------------
# 5. test_theme_dark_mode_css_variables
# ---------------------------------------------------------------------------

def test_theme_dark_mode_css_variables(e2e_page: Page) -> None:
    """Dark mode CSS variables are applied correctly."""
    page = e2e_page
    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    # Apply dark + neutral.
    page.evaluate("""
        localStorage.setItem('brieftube.theme.mode', 'dark');
        localStorage.setItem('brieftube.theme.tone', 'neutral');
    """)
    page.reload()
    page.wait_for_load_state("domcontentloaded")

    theme = page.evaluate("document.documentElement.dataset.theme")
    assert theme == "dark"

    # Check that CSS custom property --app-bg has the dark neutral value (#171717).
    app_bg = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--app-bg').trim()"
    )
    assert app_bg == "#171717", f"Expected --app-bg '#171717' in dark/neutral, got '{app_bg}'"

    # Check body background color matches the variable.
    body_bg = page.evaluate(
        "getComputedStyle(document.body).backgroundColor"
    )
    # body background should not be plain white.
    assert body_bg != "rgb(255, 255, 255)", (
        f"Body background should not be white in dark mode, got '{body_bg}'"
    )

    # Check color-scheme.
    color_scheme = page.evaluate(
        "getComputedStyle(document.documentElement).colorScheme"
    )
    assert "dark" in color_scheme, f"Expected color-scheme to contain 'dark', got '{color_scheme}'"

    # Clean up.
    page.evaluate("localStorage.setItem('brieftube.theme.mode', 'system')")


# ---------------------------------------------------------------------------
# 6. test_i18n_korean_default
# ---------------------------------------------------------------------------

def test_i18n_korean_default(e2e_page: Page) -> None:
    """Default language is Korean; Korean text appears in nav and page headings."""
    page = e2e_page
    # Ensure language is ko.
    seed_app_setting(page._e2e_db_path, "language", "ko")
    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    # Check nav links for Korean text.
    expect(page.locator('header nav a[href="/"]')).to_contain_text("영상")
    expect(page.locator('header nav a[href="/channels"]')).to_contain_text("채널")
    expect(page.locator('header nav a[href="/settings"]')).to_contain_text("설정")
    expect(page.locator('header nav a[href="/downloads"]')).to_contain_text("다운로드")
    expect(page.locator('header nav a[href="/retention"]')).to_contain_text("보관")

    # Brand name.
    expect(page.locator("header a[href='/'] span")).to_contain_text("BriefTube")

    # Page heading.
    expect(page.locator("h1")).to_contain_text("영상 목록")

    # html lang attribute.
    lang = page.evaluate("document.documentElement.lang")
    assert lang == "ko", f"Expected html lang='ko', got '{lang}'"


# ---------------------------------------------------------------------------
# 7. test_i18n_english
# ---------------------------------------------------------------------------

def test_i18n_english(e2e_page: Page) -> None:
    """Changing language to English shows English text."""
    page = e2e_page
    seed_app_setting(page._e2e_db_path, "language", "en")

    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    # Nav links should show English text.
    expect(page.locator('header nav a[href="/"]')).to_contain_text("Videos")
    expect(page.locator('header nav a[href="/channels"]')).to_contain_text("Channels")
    expect(page.locator('header nav a[href="/settings"]')).to_contain_text("Settings")
    expect(page.locator('header nav a[href="/downloads"]')).to_contain_text("Downloads")
    expect(page.locator('header nav a[href="/retention"]')).to_contain_text("Retention")

    # Page heading.
    expect(page.locator("h1")).to_contain_text("Videos")

    # html lang attribute.
    lang = page.evaluate("document.documentElement.lang")
    assert lang == "en", f"Expected html lang='en', got '{lang}'"

    # Restore Korean for subsequent tests.
    seed_app_setting(page._e2e_db_path, "language", "ko")


# ---------------------------------------------------------------------------
# 8. test_page_transition_fade
# ---------------------------------------------------------------------------

def test_page_transition_fade(e2e_page: Page) -> None:
    """Nav transition animation class is applied to [data-page-shell]."""
    page = e2e_page
    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    # Wait for DOMContentLoaded to finish binding nav transitions.
    page.wait_for_function("!!window.BrieftubeNavTransition")

    shell = page.locator("[data-page-shell]")
    expect(shell).to_be_visible()

    # After page load, the page shell should become visible (is-visible class).
    page.wait_for_function(
        "document.querySelector('[data-page-shell]').classList.contains('is-visible')",
        timeout=5000,
    )

    # Set sessionStorage flag to enable page fade on the next navigation,
    # then navigate to verify the data-page-transition attribute is set.
    page.evaluate("sessionStorage.setItem('brieftube.enableNextPageFade', '1')")
    page.goto(_url(page, "/channels"))
    page.wait_for_load_state("domcontentloaded")

    # The inline script in base.html reads the flag and sets data-page-transition="1".
    transition_attr = page.evaluate("document.documentElement.dataset.pageTransition")
    assert transition_attr == "1", (
        f"Expected data-page-transition='1' after fade-enabled navigation, got '{transition_attr}'"
    )


# ---------------------------------------------------------------------------
# 9. test_responsive_nav
# ---------------------------------------------------------------------------

def test_responsive_nav(e2e_page: Page) -> None:
    """Browser resize affects nav layout. Verify nav links remain accessible."""
    page = e2e_page
    page.goto(_url(page, "/"))
    page.wait_for_load_state("domcontentloaded")

    # Desktop viewport: nav links should be visible.
    page.set_viewport_size({"width": 1280, "height": 720})
    for href in ["/", "/channels", "/settings", "/downloads", "/queue", "/retention"]:
        link = page.locator(f'header nav a[href="{href}"]')
        expect(link).to_be_visible()

    theme_toggle = page.locator("button[data-theme-toggle]")
    expect(theme_toggle).to_be_visible()

    # Narrow viewport (mobile-like).
    page.set_viewport_size({"width": 375, "height": 667})

    # Header should still be present.
    expect(page.locator("header")).to_be_visible()

    # Nav container should still exist.
    nav = page.locator("header nav")
    expect(nav).to_be_attached()

    # At least the brand logo/link should be visible.
    # Use the specific brand link with class "flex items-center gap-2 text-white".
    brand_link = page.locator("header > div > a[href='/']")
    expect(brand_link).to_be_visible()

    # Restore viewport.
    page.set_viewport_size({"width": 1280, "height": 720})


# ---------------------------------------------------------------------------
# 10. test_healthz_endpoint
# ---------------------------------------------------------------------------

def test_healthz_endpoint(e2e_page: Page) -> None:
    """/healthz returns {"status": "ok"}."""
    page = e2e_page
    response = page.request.get(_url(page, "/healthz"))
    assert response.ok, f"Expected 200, got {response.status}"
    body = response.json()
    assert body == {"status": "ok"}, f"Unexpected healthz response: {body}"
