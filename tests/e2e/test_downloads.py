from __future__ import annotations

import re
import shutil

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.seed_helpers import (
    get_db_connection,
    seed_channel,
    seed_download_job,
    seed_video,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHANNEL_ID = "UCdownloadTestCh001"
CHANNEL_NAME = "Download Test Channel"

VIDEO_IDS = [f"dl_vid_{i:03d}" for i in range(1, 6)]
VIDEO_TITLES = [f"Download Test Video {i}" for i in range(1, 6)]

# Seed data mapping:
# VIDEO_IDS[0] → pending download job
# VIDEO_IDS[1] → running download job
# VIDEO_IDS[2] → succeeded download job (output_path set)
# VIDEO_IDS[3] → failed download job (error_code/message set)
# VIDEO_IDS[4] → no download job (used for download-from-video-detail test)


def _seed_downloads(db_path: str) -> dict[str, int]:
    """Seed channel, videos, and download jobs. Returns job IDs by status."""
    seed_channel(db_path, CHANNEL_ID, CHANNEL_NAME)
    for vid, title in zip(VIDEO_IDS, VIDEO_TITLES):
        seed_video(db_path, vid, CHANNEL_ID, title, pipeline_status="done")

    pending_id = seed_download_job(
        db_path,
        VIDEO_IDS[0],
        VIDEO_TITLES[0],
        status="pending",
        quality="1080",
    )
    running_id = seed_download_job(
        db_path,
        VIDEO_IDS[1],
        VIDEO_TITLES[1],
        status="running",
        quality="720",
    )
    succeeded_id = seed_download_job(
        db_path,
        VIDEO_IDS[2],
        VIDEO_TITLES[2],
        status="succeeded",
        quality="1080",
        output_path="/tmp/downloads/test_video_3.mp4",
        file_size_bytes=104857600,
    )
    failed_id = seed_download_job(
        db_path,
        VIDEO_IDS[3],
        VIDEO_TITLES[3],
        status="failed",
        quality="1080",
        error_code="YTDLP_EXIT_1",
        error_message="yt-dlp exited with code 1: video unavailable",
    )
    return {
        "pending": pending_id,
        "running": running_id,
        "succeeded": succeeded_id,
        "failed": failed_id,
    }


def _reset_download_seed(db_path: str) -> None:
    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM download_jobs")
    conn.execute("DELETE FROM videos WHERE channel_id = ?", (CHANNEL_ID,))
    conn.execute("DELETE FROM channels WHERE channel_id = ?", (CHANNEL_ID,))
    conn.commit()
    conn.close()


@pytest.fixture()
def downloads_seeded(e2e_server: dict) -> dict[str, int]:
    """Seed isolated download data for each test."""
    _reset_download_seed(e2e_server["db_path"])
    return _seed_downloads(e2e_server["db_path"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _goto_downloads(page: Page, status: str = "all") -> None:
    base = page._e2e_base_url
    url = f"{base}/downloads" if status == "all" else f"{base}/downloads?status={status}"
    page.goto(url)
    page.wait_for_load_state("networkidle")


def _goto_video_detail(page: Page, video_id: str) -> None:
    base = page._e2e_base_url
    page.goto(f"{base}/videos/{video_id}")
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# 1. test_downloads_page_loads
# ---------------------------------------------------------------------------
def test_downloads_page_loads(e2e_page: Page, downloads_seeded: dict) -> None:
    """The /downloads page loads and shows the status filter tabs."""
    _goto_downloads(e2e_page)

    # Page section with data attribute exists
    section = e2e_page.locator("[data-download-history-page]")
    expect(section).to_be_visible()

    # All 5 filter tabs exist (all, pending, running, succeeded, failed)
    tabs = e2e_page.locator("a[href*='/downloads?status='], a[href='/downloads?status=all']")
    expect(tabs).to_have_count(5)


# ---------------------------------------------------------------------------
# 2. test_downloads_status_filter_tabs
# ---------------------------------------------------------------------------
def test_downloads_status_filter_tabs(e2e_page: Page, downloads_seeded: dict) -> None:
    """Tab links exist with correct count badges."""
    _goto_downloads(e2e_page)

    # "all" tab should show total (4)
    all_tab = e2e_page.locator("a[href='/downloads?status=all']")
    expect(all_tab).to_contain_text("(4)")

    # Individual count badges
    pending_tab = e2e_page.locator("a[href='/downloads?status=pending']")
    expect(pending_tab).to_contain_text("(1)")

    running_tab = e2e_page.locator("a[href='/downloads?status=running']")
    expect(running_tab).to_contain_text("(1)")

    succeeded_tab = e2e_page.locator("a[href='/downloads?status=succeeded']")
    expect(succeeded_tab).to_contain_text("(1)")

    failed_tab = e2e_page.locator("a[href='/downloads?status=failed']")
    expect(failed_tab).to_contain_text("(1)")


# ---------------------------------------------------------------------------
# 3. test_downloads_status_filter_pending
# ---------------------------------------------------------------------------
def test_downloads_status_filter_pending(e2e_page: Page, downloads_seeded: dict) -> None:
    """Filtering by pending shows only pending job."""
    _goto_downloads(e2e_page, status="pending")

    # The pending tab should be active (bg-indigo-600)
    active_tab = e2e_page.locator("a[href='/downloads?status=pending']")
    expect(active_tab).to_have_class(re.compile(r"bg-indigo-600"))

    # Desktop table should show exactly 1 data row
    rows = e2e_page.locator("table tbody tr")
    expect(rows).to_have_count(1)

    # The row should contain the pending video title
    expect(rows.first).to_contain_text(VIDEO_TITLES[0])

    # Status badge should show pending class
    badge = rows.first.locator(".status-badge--transcript-pending")
    expect(badge).to_be_visible()


# ---------------------------------------------------------------------------
# 4. test_downloads_status_filter_succeeded
# ---------------------------------------------------------------------------
def test_downloads_status_filter_succeeded(e2e_page: Page, downloads_seeded: dict) -> None:
    """Filtering by succeeded shows the output_path."""
    _goto_downloads(e2e_page, status="succeeded")

    rows = e2e_page.locator("table tbody tr")
    expect(rows).to_have_count(1)

    # The row should reference the succeeded video
    expect(rows.first).to_contain_text(VIDEO_TITLES[2])

    # Output path should be visible in the result column
    result_cell = rows.first.locator(".text-emerald-700")
    expect(result_cell).to_be_visible()
    expect(result_cell).to_contain_text("/tmp/downloads/test_video_3.mp4")


# ---------------------------------------------------------------------------
# 5. test_downloads_table_and_mobile_card
# ---------------------------------------------------------------------------
def test_downloads_table_and_mobile_card(e2e_page: Page, downloads_seeded: dict) -> None:
    """Desktop table and mobile cards coexist in the DOM."""
    _goto_downloads(e2e_page)

    # Desktop table (hidden on mobile, visible md+)
    desktop_table = e2e_page.locator("table")
    expect(desktop_table).to_have_count(1)

    # Mobile cards container (visible on mobile, hidden md+)
    mobile_cards = e2e_page.locator("div.space-y-3.md\\:hidden")
    expect(mobile_cards).to_have_count(1)

    # Mobile card articles should match job count
    cards = mobile_cards.locator("article")
    expect(cards).to_have_count(4)


# ---------------------------------------------------------------------------
# 6. test_downloads_detail_modal
# ---------------------------------------------------------------------------
def test_downloads_detail_modal(e2e_page: Page, downloads_seeded: dict) -> None:
    """Clicking detail button opens the JS-rendered detail modal."""
    _goto_downloads(e2e_page)

    # Click the first detail button in the desktop table
    detail_buttons = e2e_page.locator("table [data-download-detail-open]")
    detail_buttons.first.click()

    # The download detail modal should become visible
    modal = e2e_page.locator("#download-detail-modal")
    expect(modal).to_be_visible()

    # Modal should contain detail fields
    expect(modal.locator("[data-download-detail-video-id]")).to_be_visible()
    expect(modal.locator("[data-download-detail-status]")).to_be_visible()

    # Close via the close button
    close_btn = modal.locator("[data-download-detail-close]")
    close_btn.click()

    # Modal should be hidden
    expect(modal).to_be_hidden()


# ---------------------------------------------------------------------------
# 7. test_downloads_retry_button
# ---------------------------------------------------------------------------
def test_downloads_retry_button(e2e_page: Page, downloads_seeded: dict) -> None:
    """Failed job retry button makes an API call to /api/downloads/{job_id}/retry."""
    _goto_downloads(e2e_page, status="failed")

    # Desktop table may show button; also duplicated in mobile card view
    retry_btn = e2e_page.locator("table [data-download-retry-button]").first

    job_id = retry_btn.get_attribute("data-job-id")
    assert job_id is not None

    # Intercept the retry API call and verify response.
    with e2e_page.expect_response(
        lambda resp: f"/api/downloads/{job_id}/retry" in resp.url and resp.request.method == "POST"
    ) as response_info:
        retry_btn.click()
    response = response_info.value
    assert response.ok


# ---------------------------------------------------------------------------
# 8. test_downloads_ffmpeg_warning
# ---------------------------------------------------------------------------
def test_downloads_ffmpeg_warning(e2e_page: Page, downloads_seeded: dict) -> None:
    """When ffmpeg is available, no warning banner is shown.
    When it is unavailable, the amber warning div should appear.
    We check for the ffmpeg warning div's existence based on server state.
    """
    _goto_downloads(e2e_page)

    # The ffmpeg warning banner has specific classes if present
    ffmpeg_warning = e2e_page.locator("div.border-amber-300.bg-amber-50")
    count = ffmpeg_warning.count()
    ffmpeg_installed = shutil.which("ffmpeg") is not None
    if ffmpeg_installed:
        assert count == 0, "ffmpeg installed environment must not show warning"
        return
    assert count == 1, "ffmpeg missing environment must show warning"
    expect(ffmpeg_warning).to_contain_text("ffmpeg")


# ---------------------------------------------------------------------------
# 9. test_downloads_badge_on_nav
# ---------------------------------------------------------------------------
def test_downloads_badge_on_nav(e2e_page: Page, downloads_seeded: dict) -> None:
    """Active (pending+running) jobs cause the nav badge to be updated via JS polling."""
    _goto_downloads(e2e_page)

    # The nav badge element should exist in the header
    badge = e2e_page.locator("[data-download-nav-badge]")
    expect(badge).to_have_count(1)

    # Wait for the polling to update the badge (the badge becomes visible
    # when active_count > 0, which is 2 in our seed: pending + running).
    # The JS polling fires on page load, so after networkidle it should
    # already be visible, but we allow extra time for the async fetch.
    expect(badge).to_be_visible(timeout=10_000)


# ---------------------------------------------------------------------------
# 10. test_downloads_progress_polling
# ---------------------------------------------------------------------------
def test_downloads_progress_polling(e2e_page: Page, downloads_seeded: dict) -> None:
    """JS polls /api/downloads/progress every 5 seconds."""
    base = e2e_page._e2e_base_url
    progress_requests: list[str] = []

    def on_request(req) -> None:
        if "/api/downloads/progress" in req.url and req.method == "GET":
            progress_requests.append(req.url)

    e2e_page.on("request", on_request)
    e2e_page.goto(f"{base}/downloads")
    e2e_page.wait_for_timeout(6_500)
    e2e_page.remove_listener("request", on_request)
    assert len(progress_requests) >= 2


# ---------------------------------------------------------------------------
# 11. test_download_from_video_detail
# ---------------------------------------------------------------------------
def test_download_from_video_detail(e2e_page: Page, downloads_seeded: dict) -> None:
    """On video detail page, clicking download opens a modal with quality select and submit."""
    _goto_video_detail(e2e_page, VIDEO_IDS[4])

    # The download button on the video detail page
    download_btn = e2e_page.locator("[data-video-download-open]")
    expect(download_btn).to_be_visible()

    download_btn.click()

    # The download modal should appear
    modal = e2e_page.locator("#video-download-modal")
    expect(modal).to_be_visible()

    # Quality select should be present with options
    quality_select = modal.locator("[data-download-modal-quality]")
    expect(quality_select).to_be_visible()

    # Check that quality options include common resolutions
    options = quality_select.locator("option")
    expect(options).to_have_count(5)  # 2160, 1440, 1080, 720, 480

    # Select a quality
    quality_select.select_option("720")

    # Submit button should be present
    submit_btn = modal.locator("[data-download-modal-submit]")
    expect(submit_btn).to_be_visible()

    # Intercept the download API request
    with e2e_page.expect_request(
        lambda req: f"/api/videos/{VIDEO_IDS[4]}/downloads" in req.url and req.method == "POST",
    ) as request_info:
        submit_btn.click()

    request = request_info.value
    assert request.method == "POST"

    # The modal should close after submission
    expect(modal).to_be_hidden(timeout=5_000)


# ---------------------------------------------------------------------------
# 12. test_downloads_empty_state
# ---------------------------------------------------------------------------
def test_downloads_empty_state(e2e_page: Page, downloads_seeded: dict) -> None:
    """When jobs exist, the empty-state placeholder row is absent.

    The template renders a <td colspan="6"> message inside the table only
    when the job list is empty. With our seeded data the placeholder must
    not appear, and the table must contain data rows.
    """
    _goto_downloads(e2e_page)

    # The empty-state row (colspan=6) should NOT be present
    empty_td = e2e_page.locator("table tbody td[colspan='6']")
    expect(empty_td).to_have_count(0)

    # Verify the table actually has data rows
    data_rows = e2e_page.locator("table tbody tr")
    assert data_rows.count() >= 4
