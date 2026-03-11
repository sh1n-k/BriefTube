from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request

from app.domains.downloads import enqueue_bulk_downloads
from app.i18n import DEFAULT_LANGUAGE, get_texts, normalize_language
from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.repositories import downloads as downloads_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.routers.pages_downloads import build_download_history_context
from app.routers.template_context import build_template_context

router = APIRouter(tags=["views"])
logger = logging.getLogger("app.routers.views")
DOWNLOAD_BULK_LIMIT = 100


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


async def _texts(request: Request) -> dict[str, str]:
    language = normalize_language(
        await settings_repo.get_setting(
            request.app.state.runtime.db,
            key="language",
            default=DEFAULT_LANGUAGE,
        )
    )
    return get_texts(language)


@router.get("/downloads/table")
async def download_history_fragment(
    request: Request,
    status: str = "all",
    page: int = 1,
):
    context = await build_download_history_context(
        request,
        status=status,
        page=max(1, int(page)),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/download_history.html",
        context=context,
    )


def _download_bulk_toast_header(message: str, tone: str) -> dict[str, str]:
    payload = {
        "video-download-bulk-toast": {
            "message": message,
            "tone": tone,
        }
    }
    return {"HX-Trigger": json.dumps(payload, ensure_ascii=True)}


def _resolve_download_bulk_toast_tone(
    *,
    created_count: int,
    duplicate_count: int,
    missing_count: int,
    failed_count: int,
) -> str:
    if created_count > 0:
        if failed_count > 0:
            return "warning"
        return "success"
    if duplicate_count > 0 and failed_count == 0 and missing_count == 0:
        return "info"
    if failed_count > 0 or missing_count > 0:
        return "error"
    return "info"


@router.post("/videos/download-selected")
async def download_selected_videos(request: Request):
    form = await request.form()
    selected_ids = [str(v).strip() for v in form.getlist("video_id") if str(v).strip()]
    video_ids = list(dict.fromkeys(selected_ids))
    txt = await _texts(request)

    toast_message = ""
    toast_tone = "success"
    created_count = 0
    duplicate_count = 0
    missing_count = 0
    failed_count = 0

    if not video_ids:
        toast_message = txt["download_bulk_none_selected"]
        toast_tone = "error"
    elif len(video_ids) > DOWNLOAD_BULK_LIMIT:
        toast_message = txt["download_bulk_limit_exceeded"].format(
            selected=len(video_ids),
            limit=DOWNLOAD_BULK_LIMIT,
        )
        toast_tone = "error"
    else:
        defaults = await downloads_repo.get_download_default_settings(
            request.app.state.runtime.db,
            default_output_dir=request.app.state.runtime.config.download_dir,
        )
        bulk_result = await enqueue_bulk_downloads(
            request.app.state.runtime.db,
            video_ids=video_ids,
            default_output_dir=str(defaults.get("output_dir") or ""),
            quality=str(defaults["quality"]),
            overwrite=bool(defaults["overwrite"]),
        )
        if bulk_result.had_error:
            logger.warning(
                "event=downloads.bulk_enqueue_invalid_output_dir code=%s path=%s",
                bulk_result.error_code or "download_path_invalid",
                str(defaults.get("output_dir") or ""),
                extra={
                    "event": "downloads.bulk_enqueue_invalid_output_dir",
                    "code": bulk_result.error_code or "download_path_invalid",
                },
            )
            if bulk_result.error_code == "ffmpeg_missing":
                toast_message = txt["download_toast_ffmpeg_missing"]
            else:
                toast_message = txt["download_bulk_output_dir_invalid"]
            toast_tone = "error"
            page = _safe_int(form.get("_page"), 1)
            limit_val = _safe_int(form.get("_limit"), 0)
            if limit_val <= 0:
                limit_val = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)
            sort = str(form.get("_sort") or "upload_time")
            order = str(form.get("_order") or "desc")
            channel_id = str(form.get("_channel_id") or "") or None
            raw_cat_early = form.get("_category_id")
            category_id_early = int(raw_cat_early) if raw_cat_early and str(raw_cat_early).strip().isdigit() else None
            pipeline_status_early = videos_repo.normalize_pipeline_status_filter(
                str(form.get("_pipeline_status") or "")
            )

            total = await videos_repo.count_videos(
                request.app.state.runtime.db,
                channel_id=channel_id,
                category_id=category_id_early,
                pipeline_status=pipeline_status_early,
            )
            total_pages = max(1, (total + limit_val - 1) // limit_val)
            current_page = min(max(1, page), total_pages)
            videos = await videos_repo.list_videos(
                request.app.state.runtime.db,
                channel_id=channel_id,
                sort=sort,
                order=order,
                page=current_page,
                limit=limit_val,
                category_id=category_id_early,
                pipeline_status=pipeline_status_early,
            )
            all_channels = await channels_repo.list_channels(request.app.state.runtime.db)
            channels = [ch for ch in all_channels if ch.get("category_id") == category_id_early] if category_id_early is not None else all_channels
            categories_early = await categories_repo.list_categories(request.app.state.runtime.db)
            context = await build_template_context(
                request,
                videos=videos,
                channels=channels,
                categories_for_filter=categories_early,
                status_filter_options=videos_repo.VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES,
                pagination={
                    "page": current_page,
                    "limit": limit_val,
                    "total": total,
                    "total_pages": total_pages,
                    "channel_id": channel_id or "",
                    "category_id": category_id_early if category_id_early is not None else "",
                    "pipeline_status": pipeline_status_early or "",
                    "sort": sort,
                    "order": order,
                },
            )
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="fragments/video_list.html",
                context=context,
                headers=_download_bulk_toast_header(toast_message, toast_tone),
            )
        created_count = int(bulk_result.created_count)
        duplicate_count = int(bulk_result.duplicate_count)
        missing_count = int(bulk_result.missing_count)
        failed_count = int(bulk_result.failed_count)

        if created_count > 0:
            request.app.state.runtime.download_wake_event.set()
            logger.info(
                "event=downloads.bulk_enqueue_queued selected=%s created=%s duplicate=%s missing=%s failed=%s",
                len(video_ids),
                created_count,
                duplicate_count,
                missing_count,
                failed_count,
                extra={"event": "downloads.bulk_enqueue_queued"},
            )
        else:
            logger.warning(
                "event=downloads.bulk_enqueue_none selected=%s duplicate=%s missing=%s failed=%s",
                len(video_ids),
                duplicate_count,
                missing_count,
                failed_count,
                extra={"event": "downloads.bulk_enqueue_none"},
            )
        toast_message = txt["download_bulk_result"].format(
            created=created_count,
            duplicate=duplicate_count,
            missing=missing_count,
            failed=failed_count,
        )
        toast_tone = _resolve_download_bulk_toast_tone(
            created_count=created_count,
            duplicate_count=duplicate_count,
            missing_count=missing_count,
            failed_count=failed_count,
        )

    page = _safe_int(form.get("_page"), 1)
    limit_val = _safe_int(form.get("_limit"), 0)
    if limit_val <= 0:
        limit_val = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)
    sort = str(form.get("_sort") or "upload_time")
    order = str(form.get("_order") or "desc")
    channel_id = str(form.get("_channel_id") or "") or None
    raw_cat_final = form.get("_category_id")
    category_id_final = int(raw_cat_final) if raw_cat_final and str(raw_cat_final).strip().isdigit() else None
    pipeline_status_final = videos_repo.normalize_pipeline_status_filter(str(form.get("_pipeline_status") or ""))

    total = await videos_repo.count_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        category_id=category_id_final,
        pipeline_status=pipeline_status_final,
    )
    total_pages = max(1, (total + limit_val - 1) // limit_val)
    current_page = min(max(1, page), total_pages)
    videos = await videos_repo.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=current_page,
        limit=limit_val,
        category_id=category_id_final,
        pipeline_status=pipeline_status_final,
    )
    all_channels = await channels_repo.list_channels(request.app.state.runtime.db)
    channels = [ch for ch in all_channels if ch.get("category_id") == category_id_final] if category_id_final is not None else all_channels
    categories_final = await categories_repo.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        videos=videos,
        channels=channels,
        categories_for_filter=categories_final,
        status_filter_options=videos_repo.VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES,
        pagination={
            "page": current_page,
            "limit": limit_val,
            "total": total,
            "total_pages": total_pages,
            "channel_id": channel_id or "",
            "category_id": category_id_final if category_id_final is not None else "",
            "pipeline_status": pipeline_status_final or "",
            "sort": sort,
            "order": order,
        },
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_list.html",
        context=context,
        headers=_download_bulk_toast_header(toast_message, toast_tone) if toast_message else None,
    )
