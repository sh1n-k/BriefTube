from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.repositories import channels as channels_repo
from app.repositories import downloads as downloads_repo
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.routers.helpers import build_rss_poll_preview
from app.routers.template_context import build_template_context
from app.services.downloads import is_ffmpeg_available
from app.services.telegram import build_telegram_settings_payload
from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS,
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    TRANSCRIPT_REQUEST_HEADER_PROFILE,
    compact_header_overrides,
    default_transcript_request_headers,
    format_headers_multiline,
    merge_with_default_headers,
)

router = APIRouter(tags=["pages"])


@router.get("/settings")
async def settings_page(request: Request):
    worker_settings = await settings_repo.get_worker_settings(request.app.state.runtime.db)
    videos_per_page = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)
    channel_counts = await channels_repo.count_channels_by_status(request.app.state.runtime.db)
    guard = await transcripts_repo.get_transcript_guard_state(request.app.state.runtime.db)
    transcript_header_overrides = await transcripts_repo.get_transcript_request_header_overrides(
        request.app.state.runtime.db
    )
    download_defaults = await downloads_repo.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    llm_settings = await settings_repo.get_llm_settings(request.app.state.runtime.db)
    telegram_raw_settings = await settings_repo.get_telegram_settings(request.app.state.runtime.db)
    telegram_settings = build_telegram_settings_payload(
        request.app.state.runtime.config,
        stored_bot_token=telegram_raw_settings["bot_token"],
        stored_chat_id=telegram_raw_settings["chat_id"],
    )
    compact = compact_header_overrides(transcript_header_overrides, strict=False)
    values = merge_with_default_headers(compact)
    defaults = default_transcript_request_headers()
    reset_done = request.query_params.get("guard_reset") == "1"
    context = await build_template_context(
        request,
        include_llm_runtime_status=True,
        worker_settings=worker_settings,
        videos_per_page=videos_per_page,
        rss_poll_preview=build_rss_poll_preview(
            config=request.app.state.runtime.config,
            channel_counts=channel_counts,
        ),
        transcript_guard=guard,
        transcript_request_headers={
            "profile": TRANSCRIPT_REQUEST_HEADER_PROFILE,
            "keys": list(TRANSCRIPT_REQUEST_HEADER_KEYS),
            "field_names": dict(TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS),
            "defaults": defaults,
            "values": values,
            "multiline": format_headers_multiline(values),
        },
        download_defaults=download_defaults,
        llm_settings=llm_settings,
        telegram_settings=telegram_settings,
        ffmpeg_available=is_ffmpeg_available(),
        guard_reset_done=reset_done,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=context,
    )


@router.post("/settings/transcript-guard/reset")
async def settings_reset_transcript_guard(request: Request):
    form = await request.form()
    confirmed = str(form.get("confirm_guard_reset", "")).strip().lower()
    if confirmed != "on":
        return RedirectResponse(url="/settings?guard_reset=0", status_code=303)

    await transcripts_repo.reset_transcript_guard_state(request.app.state.runtime.db)
    return RedirectResponse(url="/settings?guard_reset=1", status_code=303)


@router.get("/queue")
async def queue_page(request: Request):
    db = request.app.state.runtime.db
    transcript_items = await transcripts_repo.list_queue_items(
        db,
        transcripts_repo.TRANSCRIPT_QUEUE_STATUSES,
    )
    llm_items = await transcripts_repo.list_queue_items(
        db,
        llm_repo.LLM_QUEUE_STATUSES,
    )
    queue_counts = await transcripts_repo.queue_status(db)
    worker_settings = await settings_repo.get_worker_settings(db)
    transcript_guard = await transcripts_repo.get_transcript_guard_state(db)
    context = await build_template_context(
        request,
        transcript_items=transcript_items,
        llm_items=llm_items,
        queue_counts=queue_counts,
        worker_settings=worker_settings,
        transcript_guard=transcript_guard,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="queue.html",
        context=context,
    )
