from __future__ import annotations

import re

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


@pytest.fixture()
def empty_queue_page(e2e_page: Page) -> Page:
    """Navigate to /queue without seeding any queue videos."""
    e2e_page.goto(f"{e2e_page._e2e_base_url}/queue")
    e2e_page.wait_for_load_state("networkidle")
    return e2e_page


# --------------------------------------------------------------------------- #
# 1. Page loads with expected sections
# --------------------------------------------------------------------------- #
def test_queue_page_loads(queue_page: Page):
    """The /queue page loads and displays both transcript and LLM sections."""
    page_container = queue_page.locator("[data-queue-page]")
    expect(page_container).to_be_visible()

    # Page title section
    expect(queue_page.locator("h1")).to_be_visible()

    # Transcript section header
    transcript_title = queue_page.locator("[data-collapsible] h2").first
    expect(transcript_title).to_be_visible()

    # LLM section header
    llm_title = queue_page.locator("[data-collapsible] h2").nth(1)
    expect(llm_title).to_be_visible()


# --------------------------------------------------------------------------- #
# 2. Transcript section chip counts
# --------------------------------------------------------------------------- #
def test_queue_transcript_section(queue_page: Page):
    """Transcript queue shows correct chip counts for pending/processing/failed."""
    chips_container = queue_page.locator("[data-queue-transcript-chips]")
    expect(chips_container).to_be_visible()

    # Chips are rendered as <span> elements inside the chips container.
    # Pending chip: shows "대기 2" or "Pending 2"
    pending_chip = chips_container.locator("span", has_text=re.compile(r"2$"))
    expect(pending_chip.first).to_be_visible()

    # Processing chip: shows "... 1"
    processing_chip = chips_container.locator("span.bg-sky-100")
    expect(processing_chip).to_be_visible()
    expect(processing_chip).to_contain_text("1")

    # Failed chip: shows "... 1"
    failed_chip = chips_container.locator("span.bg-rose-100")
    expect(failed_chip).to_be_visible()
    expect(failed_chip).to_contain_text("1")

    # Total count badge
    total_count = queue_page.locator("[data-queue-transcript-count]")
    expect(total_count).to_have_text("4")  # 2 pending + 1 processing + 1 failed


# --------------------------------------------------------------------------- #
# 3. LLM section chip counts
# --------------------------------------------------------------------------- #
def test_queue_llm_section(queue_page: Page):
    """LLM queue shows correct chip counts for pending/processing/failed."""
    chips_container = queue_page.locator("[data-queue-llm-chips]")
    expect(chips_container).to_be_visible()

    # Pending chip: shows "... 2"
    pending_chip = chips_container.locator("span.bg-amber-100")
    expect(pending_chip).to_be_visible()
    expect(pending_chip).to_contain_text("2")

    # Processing chip: shows "... 1"
    processing_chip = chips_container.locator("span.bg-sky-100")
    expect(processing_chip).to_be_visible()
    expect(processing_chip).to_contain_text("1")

    # Failed chip: shows "... 1"
    failed_chip = chips_container.locator("span.bg-rose-100")
    expect(failed_chip).to_be_visible()
    expect(failed_chip).to_contain_text("1")

    # Total count badge
    total_count = queue_page.locator("[data-queue-llm-count]")
    expect(total_count).to_have_text("4")  # 2 pending + 1 processing + 1 failed


# --------------------------------------------------------------------------- #
# 4. Worker active indicator colors
# --------------------------------------------------------------------------- #
def test_queue_worker_indicators(queue_page: Page):
    """Both transcript and LLM worker indicators show active (emerald) color."""
    transcript_indicator = queue_page.locator("[data-queue-transcript-worker-indicator]")
    expect(transcript_indicator).to_be_visible()
    expect(transcript_indicator).to_have_class(re.compile(r"bg-emerald-50"))
    expect(transcript_indicator).to_have_class(re.compile(r"text-emerald-700"))

    # Inner dot should be emerald
    transcript_dot = transcript_indicator.locator("span").first
    expect(transcript_dot).to_have_class(re.compile(r"bg-emerald-500"))

    llm_indicator = queue_page.locator("[data-queue-llm-worker-indicator]")
    expect(llm_indicator).to_be_visible()
    expect(llm_indicator).to_have_class(re.compile(r"bg-emerald-50"))
    expect(llm_indicator).to_have_class(re.compile(r"text-emerald-700"))

    llm_dot = llm_indicator.locator("span").first
    expect(llm_dot).to_have_class(re.compile(r"bg-emerald-500"))


# --------------------------------------------------------------------------- #
# 5. Guard indicator — closed state (green)
# --------------------------------------------------------------------------- #
def test_queue_guard_indicator_closed(queue_page: Page):
    """Guard breaker_state=closed shows green (emerald) indicator."""
    guard_indicator = queue_page.locator("[data-queue-guard-indicator]")
    expect(guard_indicator).to_be_visible()
    expect(guard_indicator).to_have_class(re.compile(r"bg-emerald-50"))
    expect(guard_indicator).to_have_class(re.compile(r"text-emerald-700"))

    # Guard label text
    guard_label = guard_indicator.locator("[data-guard-label]")
    expect(guard_label).to_be_visible()
    # Korean "정상" or English "Closed"
    expect(guard_label).to_have_text(re.compile(r"정상|Closed"))

    # Dot color
    guard_dot = guard_indicator.locator("span:not([data-guard-label])").first
    expect(guard_dot).to_have_class(re.compile(r"bg-emerald-500"))


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


# --------------------------------------------------------------------------- #
# 10. Empty queue shows empty message
# --------------------------------------------------------------------------- #
def test_queue_empty_state(empty_queue_page: Page):
    """When no videos are in queue, empty state messages are displayed."""
    page = empty_queue_page

    # Transcript empty message
    transcript_list = page.locator("[data-queue-transcript-list]")
    expect(transcript_list).to_be_visible()
    # Korean "Transcript 대기열이 비어 있습니다." or English "Transcript queue is empty."
    transcript_empty = transcript_list.locator("p")
    expect(transcript_empty).to_be_visible()
    expect(transcript_empty).to_have_text(
        re.compile(r"Transcript.*비어 있습니다|Transcript queue is empty")
    )

    # LLM empty message
    llm_list = page.locator("[data-queue-llm-list]")
    expect(llm_list).to_be_visible()
    llm_empty = llm_list.locator("p")
    expect(llm_empty).to_be_visible()
    expect(llm_empty).to_have_text(re.compile(r"LLM.*비어 있습니다|LLM queue is empty"))

    # Total counts should be 0
    transcript_count = page.locator("[data-queue-transcript-count]")
    expect(transcript_count).to_have_text("0")

    llm_count = page.locator("[data-queue-llm-count]")
    expect(llm_count).to_have_text("0")
