from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    get_db_connection,
    seed_app_setting,
    seed_channel,
    seed_video,
)

CHANNEL_ID = "UCqueue_test_ch"
CHANNEL_NAME = "Queue Test Channel"

# Video IDs by pipeline_status
VIDEOS = {
    "tp1": "transcript_pending",
    "tp2": "transcript_pending",
    "tpr1": "transcript_processing",
    "tf1": "transcript_failed",
    "lp1": "llm_pending",
    "lp2": "llm_pending",
    "lpr1": "llm_processing",
    "lf1": "llm_failed",
}


def _seed_queue_data(db_path: str) -> None:
    """Seed 1 channel, 8 videos with various pipeline statuses, worker/guard settings."""
    seed_channel(db_path, CHANNEL_ID, CHANNEL_NAME)
    for vid, status in VIDEOS.items():
        seed_video(
            db_path,
            video_id=vid,
            channel_id=CHANNEL_ID,
            title=f"Video {vid}",
            pipeline_status=status,
        )
    seed_app_setting(db_path, "worker_transcript_enabled", "true")
    seed_app_setting(db_path, "worker_llm_enabled", "true")
    seed_app_setting(db_path, "transcript_guard_breaker_state", "closed")


def _clear_queue_data(db_path: str) -> None:
    """Remove seeded queue data to leave the DB clean for other tests."""
    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM videos WHERE channel_id = ?", (CHANNEL_ID,))
    conn.execute("DELETE FROM channels WHERE channel_id = ?", (CHANNEL_ID,))
    conn.commit()
    conn.close()


@pytest.fixture()
def queue_page(e2e_page: Page) -> Page:
    """Seed queue data, navigate to /queue, and clean up after the test."""
    db_path = e2e_page._e2e_db_path
    _seed_queue_data(db_path)
    e2e_page.goto(f"{e2e_page._e2e_base_url}/queue")
    e2e_page.wait_for_load_state("networkidle")
    yield e2e_page
    _clear_queue_data(db_path)


# --------------------------------------------------------------------------- #
# 6. Collapsible section toggle
# --------------------------------------------------------------------------- #
def test_queue_section_collapsible(queue_page: Page):
    """Clicking section header toggle collapses/expands the body."""
    # Both sections start open (data-collapsible-open attribute)
    transcript_body = queue_page.locator("[data-queue-transcript-list]")
    expect(transcript_body).to_be_visible()

    # Click the toggle to collapse
    transcript_toggle = queue_page.locator("[data-collapsible-toggle]").first
    transcript_toggle.click()

    # Body should now be hidden
    expect(transcript_body).to_be_hidden()

    # Click again to expand
    transcript_toggle.click()
    expect(transcript_body).to_be_visible()


def test_queue_clear_button_does_not_toggle_section(queue_page: Page):
    """Clicking the clear action must not collapse the queue section."""
    transcript_body = queue_page.locator("[data-queue-transcript-list]")
    expect(transcript_body).to_be_visible()

    queue_page.once("dialog", lambda dialog: dialog.dismiss())
    queue_page.locator("[data-queue-clear-section='transcript']").click()

    expect(transcript_body).to_be_visible()


# --------------------------------------------------------------------------- #
# 7. Retry button — failed video retry triggers API call and shows toast
# --------------------------------------------------------------------------- #
def test_queue_retry_button(queue_page: Page):
    """Clicking retry on a failed transcript video calls the API and shows a toast."""
    # Find the retry button for transcript_failed video (tf1)
    retry_button = queue_page.locator("[data-queue-retry-transcript='tf1']")
    expect(retry_button).to_be_visible()

    # Click retry and wait for the API response
    with queue_page.expect_response(
        lambda resp: "/api/videos/tf1/transcript/retry" in resp.url and resp.status == 200
    ) as response_info:
        retry_button.click()
    assert response_info.value.ok

    # Toast should appear with success message
    toast_stack = queue_page.locator("#ui-toast-stack")
    expect(toast_stack.locator("div").first).to_be_visible(timeout=5000)


# --------------------------------------------------------------------------- #
# 8. Nav badge shows active queue count > 0
# --------------------------------------------------------------------------- #
def test_queue_nav_badge(queue_page: Page):
    """When active queue count > 0, the nav badge should be visible with a count."""
    nav_badge = queue_page.locator("[data-queue-nav-badge]")

    # The badge is updated by JS polling. Wait for it to become visible.
    expect(nav_badge.first).to_be_visible(timeout=10_000)

    # badge_count = transcript_pending(2) + transcript_processing(1)
    #             + llm_pending(2) + llm_processing(1) = 6
    badge_text = nav_badge.first.inner_text()
    assert int(badge_text) == 6, f"Expected badge count 6, got '{badge_text}'"


# --------------------------------------------------------------------------- #
# 9. JS polling fires /api/queue/poll network request
# --------------------------------------------------------------------------- #
def test_queue_polling(queue_page: Page):
    """Queue page JS polling sends GET /api/queue/poll requests periodically."""
    responses: list[dict] = []

    def on_response(resp) -> None:
        if "/api/queue/poll" in resp.url and resp.status == 200:
            try:
                responses.append(resp.json())
            except Exception:
                responses.append({})

    queue_page.on("response", on_response)
    for _ in range(2):
        with queue_page.expect_response(
            lambda resp: "/api/queue/poll" in resp.url and resp.status == 200,
            timeout=5_000,
        ):
            pass
    queue_page.remove_listener("response", on_response)
    assert len(responses) >= 2
    payload = responses[-1]
    assert "transcript_items" in payload
    assert "llm_items" in payload
    assert "counts" in payload
    assert "badge_count" in payload
    assert "workers" in payload
    assert "transcript_guard" in payload
