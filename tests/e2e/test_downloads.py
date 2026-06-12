from __future__ import annotations

from pathlib import Path

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


def _seed_downloads(db_path: str, *, succeeded_output_path: str) -> dict[str, int | str]:
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
        output_path=succeeded_output_path,
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
        "succeeded_output_path": succeeded_output_path,
    }


def _reset_download_seed(db_path: str) -> None:
    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM download_jobs")
    conn.execute("DELETE FROM videos WHERE channel_id = ?", (CHANNEL_ID,))
    conn.execute("DELETE FROM channels WHERE channel_id = ?", (CHANNEL_ID,))
    conn.commit()
    conn.close()


@pytest.fixture()
def downloads_seeded(e2e_server: dict) -> dict[str, int | str]:
    """Seed isolated download data for each test."""
    _reset_download_seed(e2e_server["db_path"])
    succeeded_output_path = str(Path(e2e_server["download_dir"]) / "test_video_3.mp4")
    return _seed_downloads(
        e2e_server["db_path"],
        succeeded_output_path=succeeded_output_path,
    )


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
    with e2e_page.expect_response(
        lambda resp: "/api/downloads/progress" in resp.url and resp.status == 200,
        timeout=7_000,
    ):
        e2e_page.goto(f"{base}/downloads")
    with e2e_page.expect_response(
        lambda resp: "/api/downloads/progress" in resp.url and resp.status == 200,
        timeout=7_000,
    ):
        pass
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
