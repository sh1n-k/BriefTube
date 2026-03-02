"""E2E tests for system alerts toast and retention notice."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    get_db_connection,
    make_past_time,
    seed_app_setting,
    seed_channel,
    seed_system_alert,
    seed_video,
)

CHANNEL_ID = "UCtest_alert_ch01"
CHANNEL_NAME = "Alert Test Channel"


def _seed_data(db_path: str) -> None:
    """Seed 1 channel, 3 system alerts, retention setting, 2 expired videos."""
    seed_channel(db_path, CHANNEL_ID, CHANNEL_NAME)

    # 3 unacknowledged alerts: 2 rss_channel_not_found, 1 llm_config_missing
    seed_system_alert(
        db_path,
        "rss_channel_not_found",
        "RSS feed returned 404",
        channel_id=CHANNEL_ID,
        channel_name=CHANNEL_NAME,
    )
    seed_system_alert(
        db_path,
        "rss_channel_not_found",
        "RSS feed returned 404 again",
        channel_id="UCtest_alert_ch02",
        channel_name="Alert Channel Two",
    )
    seed_system_alert(
        db_path,
        "llm_config_missing",
        "LLM provider not configured",
    )

    # retention_days = 30
    seed_app_setting(db_path, "retention_days", "30")

    # 2 expired videos (uploaded 60 days ago, well past 30-day retention)
    past_time = make_past_time(60)
    seed_video(
        db_path,
        "vid_expired_01",
        CHANNEL_ID,
        "Expired Video One",
        past_time,
        pipeline_status="done",
    )
    seed_video(
        db_path,
        "vid_expired_02",
        CHANNEL_ID,
        "Expired Video Two",
        past_time,
        pipeline_status="done",
    )


def _reset_data(db_path: str) -> None:
    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM system_alerts")
    conn.execute("DELETE FROM videos")
    conn.execute("DELETE FROM channels")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _seed(e2e_server: dict) -> None:
    _reset_data(e2e_server["db_path"])
    _seed_data(e2e_server["db_path"])


# --------------------------------------------------------------------------- #
# 1. Alert toast renders when unacknowledged alerts exist
# --------------------------------------------------------------------------- #


def test_alert_toast_renders(e2e_page: Page) -> None:
    """Unacknowledged system alerts cause toast forms to appear on the page."""
    page = e2e_page
    page.goto(page._e2e_base_url)

    # There should be two alert groups: rss_channel_not_found (2) + llm_config_missing (1)
    toasts = page.locator("[data-alert-toast]")
    expect(toasts).to_have_count(2)

    # Each toast is a <form> with amber border styling
    for i in range(2):
        toast = toasts.nth(i)
        expect(toast).to_be_visible()

    # Verify the alert type hidden inputs are present
    alert_types = set()
    for i in range(2):
        val = toasts.nth(i).locator("input[name='alert_type']").input_value()
        alert_types.add(val)
    assert alert_types == {"rss_channel_not_found", "llm_config_missing"}


# --------------------------------------------------------------------------- #
# 2. Dismiss click removes the toast from DOM
# --------------------------------------------------------------------------- #


def test_alert_toast_dismiss(e2e_page: Page) -> None:
    """Clicking the dismiss (X) button removes the toast from the DOM."""
    page = e2e_page
    page.goto(page._e2e_base_url)

    toasts = page.locator("[data-alert-toast]")
    expect(toasts).to_have_count(2)

    # Click the dismiss button on the first toast
    first_toast = toasts.first
    dismiss_btn = first_toast.locator("[data-alert-dismiss]")
    dismiss_btn.click()

    # After the 150ms fade-out + removal, only 1 toast should remain
    expect(toasts).to_have_count(1)


# --------------------------------------------------------------------------- #
# 3. Confirm checkbox + submit → HTMX POST → toast removed
# --------------------------------------------------------------------------- #


def test_alert_confirm_and_ack(e2e_page: Page) -> None:
    """Checking the confirm box enables the submit button; submitting removes the toast via HTMX."""
    page = e2e_page
    page.goto(page._e2e_base_url)

    toasts = page.locator("[data-alert-toast]")
    # After the previous dismiss test, alerts may have changed state.
    # We work with whatever toasts are present.
    initial_count = toasts.count()
    assert initial_count > 0, "Expected at least one alert toast to be present"

    # Find the first toast with the llm_config_missing type (has only 1 member)
    target_toast = None
    for i in range(initial_count):
        toast = toasts.nth(i)
        alert_type_val = toast.locator("input[name='alert_type']").input_value()
        if alert_type_val == "llm_config_missing":
            target_toast = toast
            break

    # If llm_config_missing was already dismissed, use any remaining toast
    if target_toast is None:
        target_toast = toasts.first

    # Submit button should be disabled initially
    submit_btn = target_toast.locator("[data-alert-submit]")
    expect(submit_btn).to_be_disabled()

    # Check the confirm checkbox
    confirm_checkbox = target_toast.locator("[data-alert-confirm]")
    confirm_checkbox.check()

    # Submit button should now be enabled
    expect(submit_btn).to_be_enabled()

    # Click submit — HTMX POST to /views/alerts/ack-group with hx-swap="outerHTML"
    # The form should be replaced (removed) from DOM
    submit_btn.click()

    # Wait for the toast to be removed from the DOM
    expect(target_toast).to_have_count(0)


# --------------------------------------------------------------------------- #
# 4. <details> toggle shows member list
# --------------------------------------------------------------------------- #


def test_alert_group_members_toggle(e2e_page: Page) -> None:
    """Opening the <details> element reveals the member list."""
    page = e2e_page
    page.goto(page._e2e_base_url)

    toasts = page.locator("[data-alert-toast]")
    toast_count = toasts.count()
    assert toast_count > 0, "Expected at least one alert toast to be present"

    # Use the first available toast
    toast = toasts.first

    # The <details> element is inside the toast
    details = toast.locator("details")
    expect(details).to_be_visible()

    # The member list (<ul>) should not be visible initially (details is closed)
    member_list = details.locator("ul")
    expect(member_list).to_have_count(1)
    expect(member_list).to_be_hidden()

    # Click the <summary> to open the details
    summary = details.locator("summary")
    summary.click()

    # Now the member list should be visible
    expect(member_list).to_be_visible()

    # There should be at least 1 <li> member entry
    members = member_list.locator("li")
    assert members.count() >= 1


# --------------------------------------------------------------------------- #
# 5. Retention notice toast renders and links to /retention
# --------------------------------------------------------------------------- #


def test_retention_notice_toast(e2e_page: Page) -> None:
    """Retention notice toast appears when expired videos exist and links to /retention."""
    page = e2e_page
    page.goto(page._e2e_base_url)

    # The retention notice is rendered with data-retention-notice attribute
    notice = page.locator("[data-retention-notice]")
    expect(notice).to_be_visible()

    # It should contain a link to /retention
    link = notice.locator("a[href='/retention']")
    expect(link).to_be_visible()

    # Click the link to navigate to the retention page
    link.click()

    # Should navigate to /retention
    page.wait_for_url(re.compile(r"/retention"))
    expect(page).to_have_url(re.compile(r"/retention"))
