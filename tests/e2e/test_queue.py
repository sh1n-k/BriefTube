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
    expect(e2e_page.locator("[data-queue-page]")).to_be_visible()
    yield e2e_page
    _clear_queue_data(db_path)


# --------------------------------------------------------------------------- #
# 6. Collapsible section toggle and clear action isolation
# --------------------------------------------------------------------------- #
def test_queue_section_collapsible_and_clear_action(queue_page: Page):
    """Section toggles normally while its clear action does not collapse it."""
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

    queue_page.once("dialog", lambda dialog: dialog.dismiss())
    queue_page.locator("[data-queue-clear-section='transcript']").click()

    expect(transcript_body).to_be_visible()


# --------------------------------------------------------------------------- #
# 7. Retry, nav badge, and periodic polling
# --------------------------------------------------------------------------- #
def test_queue_retry_badge_and_polling(e2e_page: Page):
    """Queue polling updates its badge and retry remains functional."""
    db_path = e2e_page._e2e_db_path
    _seed_queue_data(db_path)
    e2e_page.clock.install()
    try:
        with e2e_page.expect_response(
            lambda resp: "/api/queue/poll" in resp.url and resp.status == 200,
        ):
            e2e_page.goto(f"{e2e_page._e2e_base_url}/queue")
        expect(e2e_page.locator("[data-queue-page]")).to_be_visible()

        nav_badge = e2e_page.locator("[data-queue-nav-badge]").first
        expect(nav_badge).to_be_visible()
        expect(nav_badge).to_have_text("6")

        payload: dict = {}
        for _ in range(2):
            with e2e_page.expect_response(
                lambda resp: "/api/queue/poll" in resp.url and resp.status == 200,
            ) as response_info:
                e2e_page.clock.run_for(2_000)
            payload = response_info.value.json()

        assert "transcript_items" in payload
        assert "llm_items" in payload
        assert "counts" in payload
        assert "badge_count" in payload
        assert "workers" in payload
        assert "transcript_guard" in payload

        retry_button = e2e_page.locator("[data-queue-retry-transcript='tf1']")
        expect(retry_button).to_be_visible()
        with e2e_page.expect_response(
            lambda resp: "/api/videos/tf1/transcript/retry" in resp.url and resp.status == 200
        ) as response_info:
            retry_button.click()
        assert response_info.value.ok
        expect(e2e_page.locator("#ui-toast-stack div").first).to_be_visible()
    finally:
        _clear_queue_data(db_path)
