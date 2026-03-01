from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from app import repository
from app.routers.template_context import build_template_context
from app.services.downloads import is_ffmpeg_available
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
REACTIVATE_BATCH_LIMIT = 50
DOWNLOAD_PAGE_LIMIT = 50


@router.get("/")
async def home(
    request: Request,
    channel_id: str | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    if limit is None:
        limit = await repository.get_videos_per_page_setting(request.app.state.runtime.db)

    channels = await repository.list_channels(request.app.state.runtime.db)
    total = await repository.count_videos(request.app.state.runtime.db, channel_id=channel_id)
    total_pages = max(1, (total + limit - 1) // limit)
    current_page = min(max(1, page), total_pages)
    videos = await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=current_page,
        limit=limit,
    )

    context = await build_template_context(
        request,
        channels=channels,
        videos=videos,
        pagination={
            "page": current_page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "channel_id": channel_id or "",
            "sort": sort,
            "order": order,
        },
    )
    return request.app.state.templates.TemplateResponse(request=request, name="index.html", context=context)


@router.get("/videos/{video_id}")
async def video_page(video_id: str, request: Request):
    detail = await repository.get_video_detail(request.app.state.runtime.db, video_id)
    transcript_retry_done = request.query_params.get("transcript_retry") == "1"
    download_defaults = await repository.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    context = await build_template_context(
        request,
        video=detail,
        transcript_retry_done=transcript_retry_done,
        download_defaults=download_defaults,
        ffmpeg_available=is_ffmpeg_available(),
    )
    return request.app.state.templates.TemplateResponse(request=request, name="video_detail.html", context=context)


@router.get("/channels")
async def channel_page(
    request: Request,
    status: str = Query(default=repository.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
):
    channel_status = repository.normalize_channel_management_status(status)
    channels = await repository.list_channels_for_management(
        request.app.state.runtime.db,
        status=channel_status,
    )
    channel_counts = await repository.count_channels_by_status(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        reactivate_batch_limit=REACTIVATE_BATCH_LIMIT,
        reactivate_probe_timeout_seconds=max(
            1,
            int(request.app.state.runtime.config.rss_timeout_seconds),
        ),
    )
    return request.app.state.templates.TemplateResponse(request=request, name="channels.html", context=context)


@router.get("/settings")
async def settings_page(request: Request):
    worker_settings = await repository.get_worker_settings(request.app.state.runtime.db)
    videos_per_page = await repository.get_videos_per_page_setting(request.app.state.runtime.db)
    guard = await repository.get_transcript_guard_state(request.app.state.runtime.db)
    transcript_header_overrides = await repository.get_transcript_request_header_overrides(
        request.app.state.runtime.db
    )
    download_defaults = await repository.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    llm_settings = await repository.get_llm_settings(request.app.state.runtime.db)
    compact = compact_header_overrides(transcript_header_overrides, strict=False)
    values = merge_with_default_headers(compact)
    defaults = default_transcript_request_headers()
    reset_done = request.query_params.get("guard_reset") == "1"
    context = await build_template_context(
        request,
        include_llm_runtime_status=True,
        worker_settings=worker_settings,
        videos_per_page=videos_per_page,
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

    await repository.reset_transcript_guard_state(request.app.state.runtime.db)
    return RedirectResponse(url="/settings?guard_reset=1", status_code=303)


@router.post("/videos/{video_id}/transcript/retry")
async def retry_transcript(video_id: str, request: Request):
    affected = await repository.reset_transcript_for_retry(request.app.state.runtime.db, video_id)
    retry_flag = "1" if affected > 0 else "0"
    return RedirectResponse(url=f"/videos/{video_id}?transcript_retry={retry_flag}", status_code=303)


@router.get("/retention")
async def retention_page(request: Request):
    policy = await repository.get_policy_settings(request.app.state.runtime.db)
    expired_videos = await repository.list_retention_expired_videos(
        request.app.state.runtime.db,
        retention_days=policy["retention_days"],
    )
    deleted_raw = request.query_params.get("deleted", "0")
    try:
        deleted_count = max(0, int(deleted_raw))
    except (TypeError, ValueError):
        deleted_count = 0
    context = await build_template_context(
        request,
        expired_videos=expired_videos,
        retention_days=policy["retention_days"],
        deleted_count=deleted_count,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="retention.html",
        context=context,
    )


@router.get("/downloads")
async def downloads_page(
    request: Request,
    status: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
):
    normalized_status = repository.normalize_download_status_filter(status)
    jobs = await repository.list_download_jobs(
        request.app.state.runtime.db,
        status=normalized_status,
        page=page,
        limit=DOWNLOAD_PAGE_LIMIT,
    )
    total = await repository.count_download_jobs(
        request.app.state.runtime.db,
        status=normalized_status,
    )
    counts = await repository.count_download_jobs_by_status(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        jobs=jobs,
        download_status=normalized_status,
        download_total=total,
        download_page=page,
        download_limit=DOWNLOAD_PAGE_LIMIT,
        download_counts=counts,
        ffmpeg_available=is_ffmpeg_available(),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="downloads.html",
        context=context,
    )


def _cleanup_thumbnail_files(thumbnail_paths: list[str], thumbnail_dir: str) -> None:
    base_dir = Path(thumbnail_dir).resolve()
    for raw_path in thumbnail_paths:
        filename = Path(raw_path).name
        if not filename:
            continue
        target = (base_dir / filename).resolve()
        if target.parent != base_dir:
            continue
        try:
            if target.exists() and target.is_file():
                target.unlink()
        except OSError:
            continue


@router.post("/retention/delete-selected")
async def delete_retention_selected(request: Request):
    form = await request.form()
    selected = [str(value).strip() for value in form.getlist("video_id") if str(value).strip()]
    if not selected:
        return RedirectResponse(url="/retention?deleted=0", status_code=303)

    policy = await repository.get_policy_settings(request.app.state.runtime.db)
    expired_ids = set(
        await repository.list_retention_expired_video_ids(
            request.app.state.runtime.db,
            retention_days=policy["retention_days"],
        )
    )
    targets = [video_id for video_id in selected if video_id in expired_ids]

    result = await repository.delete_videos_by_ids(request.app.state.runtime.db, targets)
    _cleanup_thumbnail_files(
        result["thumbnail_paths"],
        request.app.state.runtime.config.thumbnail_dir,
    )
    return RedirectResponse(url=f"/retention?deleted={result['deleted']}", status_code=303)


@router.post("/retention/delete-all")
async def delete_retention_all(request: Request):
    form = await request.form()
    confirmed = str(form.get("confirm_delete_all", "")).strip().lower()
    if confirmed != "on":
        return RedirectResponse(url="/retention", status_code=303)
    policy = await repository.get_policy_settings(request.app.state.runtime.db)
    expired_ids = await repository.list_retention_expired_video_ids(
        request.app.state.runtime.db,
        retention_days=policy["retention_days"],
    )
    result = await repository.delete_videos_by_ids(request.app.state.runtime.db, expired_ids)
    _cleanup_thumbnail_files(
        result["thumbnail_paths"],
        request.app.state.runtime.config.thumbnail_dir,
    )
    return RedirectResponse(url=f"/retention?deleted={result['deleted']}", status_code=303)
