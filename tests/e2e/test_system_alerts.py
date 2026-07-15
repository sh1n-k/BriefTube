"""E2E tests for system alerts toast and retention notice."""

from __future__ import annotations

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
# 2. Dismiss click removes the toast from DOM
# --------------------------------------------------------------------------- #


def test_alert_dismiss_and_acknowledge(e2e_page: Page) -> None:
    """Dismiss and confirmed acknowledgement both remove alert toasts."""
    page = e2e_page
    page.goto(page._e2e_base_url)

    toasts = page.locator("[data-alert-toast]")
    expect(toasts).to_have_count(2)

    first_toast = toasts.first
    details = first_toast.locator("details")
    members = details.locator("ul")
    expect(members).to_be_hidden()
    details.locator("summary").click()
    expect(members).to_be_visible()
    assert members.locator("li").count() >= 1

    dismiss_btn = first_toast.locator("[data-alert-dismiss]")
    dismiss_btn.click()

    expect(toasts).to_have_count(1)
    initial_count = toasts.count()
    assert initial_count > 0, "Expected at least one alert toast to be present"
    target_toast = None
    for i in range(initial_count):
        toast = toasts.nth(i)
        alert_type_val = toast.locator("input[name='alert_type']").input_value()
        if alert_type_val == "llm_config_missing":
            target_toast = toast
            break

    if target_toast is None:
        target_toast = toasts.first
    submit_btn = target_toast.locator("[data-alert-submit]")
    expect(submit_btn).to_be_disabled()
    confirm_checkbox = target_toast.locator("[data-alert-confirm]")
    confirm_checkbox.check()
    expect(submit_btn).to_be_enabled()
    submit_btn.click()
    expect(target_toast).to_have_count(0)
