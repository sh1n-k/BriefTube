from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.dialog_helpers import (
    cancel_app_dialog,
    confirm_app_dialog,
    expect_app_dialog_open,
)
from tests.e2e.seed_helpers import (
    get_db_connection,
    make_past_time,
    seed_channel,
    seed_transcript,
    seed_video,
)

CHANNEL_ID = "UC_REFETCH_CHANNEL_001"
VIDEO_ID = "REFETCH_VIDEO_01"


@pytest.fixture(scope="module")
def seeded_server(e2e_server: dict) -> dict:
    db_path = e2e_server["db_path"]
    seed_channel(db_path, CHANNEL_ID, "Refetch Channel")
    seed_video(
        db_path,
        VIDEO_ID,
        CHANNEL_ID,
        "Refetch Video",
        upload_time=make_past_time(1),
        pipeline_status="paused",
    )
    seed_transcript(db_path, VIDEO_ID, "Foreign! Foreign! broken transcript", language="en")
    return e2e_server


def _transcript_jobs(db_path: str) -> list[tuple[str, str]]:
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT status, origin FROM video_jobs WHERE video_id = ? AND kind = 'transcript'",
            (VIDEO_ID,),
        ).fetchall()
        return [tuple(row) for row in rows]
    finally:
        conn.close()


@pytest.mark.e2e
def test_transcript_refetch_button_confirms_then_queues(
    e2e_page: Page, seeded_server: dict
) -> None:
    page = e2e_page
    page.goto(f"{seeded_server['base_url']}/videos/{VIDEO_ID}")
    page.wait_for_selector("#video-detail-wrap")

    button = page.locator("[data-video-transcript-refetch-button]")
    expect(button).to_be_visible()

    # Cancelling the modal sends nothing and leaves the button usable.
    button.click()
    expect_app_dialog_open(page, text="덮어씁니다")
    cancel_app_dialog(page)
    expect(button).to_be_enabled()
    assert _transcript_jobs(seeded_server["db_path"]) == []

    button.click()
    expect_app_dialog_open(page)
    with page.expect_response(
        lambda resp: f"/views/videos/{VIDEO_ID}/transcript-refetch" in resp.url
    ):
        confirm_app_dialog(page)

    expect(page.locator("body")).to_contain_text("자막 재수집 요청을 등록했습니다")
    expect(page.locator("[data-manual-transcript-status='pending']")).to_be_visible()
    expect(page.locator("[data-video-transcript-refetch-button]")).to_have_count(0)
    assert _transcript_jobs(seeded_server["db_path"]) == [("queued", "manual")]
