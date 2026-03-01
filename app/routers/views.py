from __future__ import annotations

import json
from pathlib import Path
import logging

from fastapi import APIRouter, Form, Query, Request, Response
import httpx
from starlette.datastructures import UploadFile

from app import repository
from app.domains.downloads import enqueue_bulk_downloads
from app.i18n import DEFAULT_LANGUAGE, get_texts, normalize_language
from app.routers.template_context import build_template_context
from app.services.bulk_channels import (
    collect_inputs_from_sources,
    parse_takeout_entries,
    resolve_bulk_inputs,
)
from app.services.downloads import is_ffmpeg_available
from app.services.llm_runtime import LlmRuntimeStatus, is_runtime_ready_for_resume

router = APIRouter(prefix="/views", tags=["views"])
logger = logging.getLogger(__name__)
REACTIVATE_BATCH_LIMIT = 50
DOWNLOAD_BULK_LIMIT = 100


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


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


def _unpack_candidate(value: str) -> tuple[str, str] | None:
    packed = value.strip()
    if "|||" not in packed:
        return None
    channel_id, channel_name = packed.split("|||", 1)
    normalized_id = channel_id.strip()
    normalized_name = channel_name.strip()
    if not normalized_id or not normalized_name:
        return None
    return normalized_id, normalized_name


async def _texts(request: Request) -> dict[str, str]:
    language = normalize_language(
        await repository.get_setting(
            request.app.state.runtime.db,
            key="language",
            default=DEFAULT_LANGUAGE,
        )
    )
    return get_texts(language)


async def _resolve_channel_management_state(
    request: Request,
    raw_status: str | None,
    category_id: int | None = None,
) -> tuple[str, list[dict[str, object]], dict[str, int]]:
    channel_status = repository.normalize_channel_management_status(raw_status)
    channels = await repository.list_channels_for_management(
        request.app.state.runtime.db,
        status=channel_status,
        category_id=category_id,
    )
    channel_counts = await repository.count_channels_by_status(request.app.state.runtime.db)
    return channel_status, channels, channel_counts


def _channel_management_ui_context(request: Request) -> dict[str, int]:
    return {
        "reactivate_batch_limit": REACTIVATE_BATCH_LIMIT,
        "reactivate_probe_timeout_seconds": max(
            1,
            int(request.app.state.runtime.config.rss_timeout_seconds),
        ),
    }


def _reactivate_toast_header(message: str, tone: str) -> dict[str, str]:
    payload = {
        "channel-reactivate-toast": {
            "message": message,
            "tone": tone,
        }
    }
    return {"HX-Trigger": json.dumps(payload)}


def _download_bulk_toast_header(message: str, tone: str) -> dict[str, str]:
    payload = {
        "video-download-bulk-toast": {
            "message": message,
            "tone": tone,
        }
    }
    return {"HX-Trigger": json.dumps(payload)}


def _llm_runtime_toast_header(message: str, tone: str) -> dict[str, str]:
    payload = {
        "llm-runtime-toast": {
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
    if failed_count > 0 or missing_count > 0:
        return "error"
    if created_count > 0:
        return "success"
    if duplicate_count > 0:
        return "info"
    return "error"


def _format_reactivate_failure_reason(txt: dict[str, str], reason_code: str) -> str:
    if reason_code.startswith("http_"):
        code = reason_code.split("_", 1)[1]
        return txt["channel_reactivate_reason_http"].format(code=code)
    if reason_code.startswith("error_"):
        error_name = reason_code.split("_", 1)[1]
        return txt["channel_reactivate_reason_exception"].format(name=error_name)
    return txt["channel_reactivate_reason_unknown"]


def _format_failed_channel_preview(txt: dict[str, str], labels: list[str]) -> str:
    preview = labels[:3]
    remaining = max(0, len(labels) - len(preview))
    if remaining > 0:
        preview.append(txt["channel_reactivate_bulk_more"].format(count=remaining))
    return ", ".join(preview)


async def _probe_channel_reactivation(
    request: Request,
    channel_id: str,
    feed_mode: str,
) -> tuple[bool, str]:
    cache = request.app.state.runtime.rss_cache.get(channel_id, {})
    if cache.get("feed_mode", "") != feed_mode:
        etag, last_modified = None, None
    else:
        etag = cache.get("etag")
        last_modified = cache.get("last_modified")

    logger.debug(
        "event=channels.reactivate_probe_start channel_id=%s feed_mode=%s has_etag=%s has_last_modified=%s",
        channel_id,
        feed_mode,
        bool(etag),
        bool(last_modified),
        extra={"event": "channels.reactivate_probe_start"},
    )
    try:
        _, new_etag, new_last_modified = await request.app.state.runtime.rss_service.fetch_channel_feed(
            channel_id=channel_id,
            etag=etag,
            last_modified=last_modified,
            feed_mode=feed_mode,
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        logger.debug(
            "event=channels.reactivate_probe_failed channel_id=%s reason=http status=%s",
            channel_id,
            status_code,
            extra={"event": "channels.reactivate_probe_failed", "code": str(status_code or "-")},
        )
        if status_code is not None:
            return False, f"http_{status_code}"
        return False, "http_unknown"
    except Exception as exc:
        logger.debug(
            "event=channels.reactivate_probe_failed channel_id=%s reason=exception error_type=%s",
            channel_id,
            exc.__class__.__name__,
            extra={"event": "channels.reactivate_probe_failed", "code": exc.__class__.__name__},
        )
        return False, f"error_{exc.__class__.__name__}"

    request.app.state.runtime.rss_cache[channel_id] = {
        "etag": new_etag or "",
        "last_modified": new_last_modified or "",
        "feed_mode": feed_mode,
    }
    logger.debug(
        "event=channels.reactivate_probe_ok channel_id=%s feed_mode=%s new_etag=%s new_last_modified=%s",
        channel_id,
        feed_mode,
        bool(new_etag),
        bool(new_last_modified),
        extra={"event": "channels.reactivate_probe_ok"},
    )
    return True, ""


@router.get("/channel-list")
async def channel_list(
    request: Request,
    status: str = Query(default=repository.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
    category_id: int | None = Query(default=None),
):
    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request, status, category_id=category_id,
    )
    categories = await repository.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        categories=categories,
        selected_category_id=category_id,
        **_channel_management_ui_context(request),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context=context,
    )


@router.post("/channels/add")
async def add_channel(request: Request):
    form = await request.form()
    requested_status = repository.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    selected_candidate = str(form.get("selected_candidate", "")).strip()
    source = str(form.get("source", "")).strip()
    txt = await _texts(request)

    if selected_candidate:
        unpacked = _unpack_candidate(selected_candidate)
        if not unpacked:
            context = await build_template_context(
                request,
                add_mode="error",
                add_message=txt["channel_add_invalid_selection"],
                add_source=source,
                add_candidates=[],
                add_status=requested_status,
            )
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="fragments/channel_add_result.html",
                context=context,
            )

        channel_id, channel_name = unpacked
        await repository.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        channel_status, channels, channel_counts = await _resolve_channel_management_state(
            request,
            requested_status,
        )
        context = await build_template_context(
            request,
            add_mode="success",
            add_message=txt["channel_add_saved"],
            add_source="",
            add_candidates=[],
            channels=channels,
            channel_status=channel_status,
            channel_counts=channel_counts,
            add_status=channel_status,
            **_channel_management_ui_context(request),
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/channel_add_result.html",
            context=context,
        )

    if not source:
        context = await build_template_context(
            request,
            add_mode="error",
            add_message=txt["channel_add_empty_input"],
            add_source="",
            add_candidates=[],
            add_status=requested_status,
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/channel_add_result.html",
            context=context,
        )

    try:
        resolved = await request.app.state.runtime.channel_resolver.resolve_input(source)
    except Exception as exc:
        logger.warning(
            "event=channels.single_resolve_error source=%s error_type=%s",
            source,
            exc.__class__.__name__,
        )
        context = await build_template_context(
            request,
            add_mode="error",
            add_message=txt["channel_add_resolve_error"],
            add_source=source,
            add_candidates=[],
            add_status=requested_status,
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/channel_add_result.html",
            context=context,
        )

    status = resolved.get("status")
    if status == "resolved":
        item = resolved.get("resolved") or {}
        channel_id = str(item.get("channel_id", "")).strip()
        channel_name = str(item.get("channel_name", "")).strip()
        if channel_id and channel_name:
            await repository.add_channel(
                request.app.state.runtime.db,
                channel_id=channel_id,
                channel_name=channel_name,
            )
            channel_status, channels, channel_counts = await _resolve_channel_management_state(
                request,
                requested_status,
            )
            context = await build_template_context(
                request,
                add_mode="success",
                add_message=txt["channel_add_saved"],
                add_source="",
                add_candidates=[],
                channels=channels,
                channel_status=channel_status,
                channel_counts=channel_counts,
                add_status=channel_status,
                **_channel_management_ui_context(request),
            )
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="fragments/channel_add_result.html",
                context=context,
            )

    if status == "needs_selection":
        context = await build_template_context(
            request,
            add_mode="selection",
            add_message=txt["channel_add_needs_selection"],
            add_source=source,
            add_candidates=resolved.get("candidates", []),
            add_status=requested_status,
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/channel_add_result.html",
            context=context,
        )

    context = await build_template_context(
        request,
        add_mode="error",
        add_message=txt["channel_add_failed"],
        add_source=source,
        add_candidates=[],
        add_reason=str(resolved.get("reason", "")).strip(),
        add_status=requested_status,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_add_result.html",
        context=context,
    )


@router.post("/channels/delete-selected")
async def delete_selected_channels(request: Request):
    form = await request.form()
    requested_status = repository.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    channel_ids = [str(value).strip() for value in form.getlist("channel_id") if str(value).strip()]
    result = await repository.delete_channels_with_related_data(
        request.app.state.runtime.db,
        channel_ids,
    )
    _cleanup_thumbnail_files(
        result["thumbnail_paths"],
        request.app.state.runtime.config.thumbnail_dir,
    )
    for channel_id in channel_ids:
        request.app.state.runtime.rss_cache.pop(channel_id, None)

    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request,
        requested_status,
    )
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        **_channel_management_ui_context(request),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context=context,
    )


@router.post("/channels/{channel_id}/delete")
async def delete_single_channel(
    channel_id: str,
    request: Request,
    status: str = Query(default=repository.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
):
    normalized = channel_id.strip()
    requested_status = repository.normalize_channel_management_status(status)
    result = await repository.delete_channels_with_related_data(
        request.app.state.runtime.db,
        [normalized],
    )
    _cleanup_thumbnail_files(
        result["thumbnail_paths"],
        request.app.state.runtime.config.thumbnail_dir,
    )
    request.app.state.runtime.rss_cache.pop(normalized, None)

    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request,
        requested_status,
    )
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        **_channel_management_ui_context(request),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context=context,
    )


@router.post("/channels/reactivate-selected")
async def reactivate_selected_channels(request: Request):
    form = await request.form()
    requested_status = repository.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    bulk_action = str(form.get("bulk_action", "")).strip().lower()
    channel_ids = [str(value).strip() for value in form.getlist("channel_id") if str(value).strip()]
    logger.debug(
        "event=channels.reactivate_bulk_requested status=%s bulk_action=%s selected_count=%s",
        requested_status,
        bulk_action or "reactivate",
        len(channel_ids),
        extra={"event": "channels.reactivate_bulk_requested"},
    )
    toast_message = ""
    toast_tone = "success"
    if bulk_action == "delete":
        result = await repository.delete_channels_with_related_data(
            request.app.state.runtime.db,
            channel_ids,
        )
        _cleanup_thumbnail_files(
            result["thumbnail_paths"],
            request.app.state.runtime.config.thumbnail_dir,
        )
        for channel_id in channel_ids:
            request.app.state.runtime.rss_cache.pop(channel_id, None)
        logger.debug(
            "event=channels.reactivate_bulk_delete_done requested=%s deleted=%s",
            len(channel_ids),
            result["deleted_channels"],
            extra={"event": "channels.reactivate_bulk_delete_done"},
        )
    else:
        txt = await _texts(request)
        if not channel_ids:
            toast_message = txt["channel_reactivate_bulk_none_selected"]
            toast_tone = "error"
        elif len(channel_ids) > REACTIVATE_BATCH_LIMIT:
            toast_message = txt["channel_reactivate_limit_exceeded"].format(
                selected=len(channel_ids),
                limit=REACTIVATE_BATCH_LIMIT,
            )
            toast_tone = "error"
            logger.warning(
                "event=channels.reactivate_bulk_limited selected=%s limit=%s",
                len(channel_ids),
                REACTIVATE_BATCH_LIMIT,
                extra={"event": "channels.reactivate_bulk_limited", "code": str(REACTIVATE_BATCH_LIMIT)},
            )
        else:
            policy = await repository.get_policy_settings(request.app.state.runtime.db)
            feed_mode = str(policy.get("rss_feed_mode", repository.RSS_FEED_MODE_DEFAULT))
            channel_name_map = await repository.get_channel_name_map(
                request.app.state.runtime.db,
                channel_ids,
            )
            success_ids: list[str] = []
            failed: list[tuple[str, str]] = []
            for channel_id in channel_ids:
                ok, reason_code = await _probe_channel_reactivation(
                    request,
                    channel_id,
                    feed_mode,
                )
                if ok:
                    success_ids.append(channel_id)
                else:
                    failed.append((channel_id, reason_code))

            await repository.reactivate_channels(request.app.state.runtime.db, success_ids)

            success_count = len(success_ids)
            failed_labels = [channel_name_map.get(channel_id, channel_id) for channel_id, _ in failed]
            if not failed:
                toast_message = txt["channel_reactivate_bulk_success"].format(success=success_count)
                toast_tone = "success"
            else:
                failed_count = len(failed)
                failed_preview = _format_failed_channel_preview(txt, failed_labels)
                toast_key = (
                    "channel_reactivate_bulk_failed"
                    if success_count == 0
                    else "channel_reactivate_bulk_partial"
                )
                toast_message = txt[toast_key].format(
                    success=success_count,
                    failed=failed_count,
                    channels=failed_preview,
                )
                toast_tone = "error"
            logger.debug(
                "event=channels.reactivate_bulk_result selected=%s success=%s failed=%s",
                len(channel_ids),
                success_count,
                len(failed),
                extra={"event": "channels.reactivate_bulk_result"},
            )
            if failed:
                logger.debug(
                    "event=channels.reactivate_bulk_failed_ids ids=%s",
                    ",".join(channel_id for channel_id, _ in failed),
                    extra={"event": "channels.reactivate_bulk_failed_ids"},
                )

    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request,
        requested_status,
    )
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        **_channel_management_ui_context(request),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context=context,
        headers=(
            _reactivate_toast_header(toast_message, toast_tone)
            if bulk_action != "delete" and toast_message
            else None
        ),
    )


@router.post("/channels/{channel_id}/reactivate")
async def reactivate_single_channel(
    channel_id: str,
    request: Request,
    status: str = Query(default=repository.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
):
    normalized = channel_id.strip()
    requested_status = repository.normalize_channel_management_status(status)
    logger.debug(
        "event=channels.reactivate_single_requested channel_id=%s status=%s",
        normalized,
        requested_status,
        extra={"event": "channels.reactivate_single_requested"},
    )
    txt = await _texts(request)
    channel_name_map = await repository.get_channel_name_map(
        request.app.state.runtime.db,
        [normalized],
    )
    channel_label = channel_name_map.get(normalized, normalized)
    policy = await repository.get_policy_settings(request.app.state.runtime.db)
    feed_mode = str(policy.get("rss_feed_mode", repository.RSS_FEED_MODE_DEFAULT))
    ok, reason_code = await _probe_channel_reactivation(request, normalized, feed_mode)
    if ok:
        await repository.reactivate_channel(request.app.state.runtime.db, normalized)
        logger.info(
            "event=channels.reactivate_single_success channel_id=%s channel_name=%s",
            normalized,
            channel_label,
            extra={"event": "channels.reactivate_single_success"},
        )
        toast_message = txt["channel_reactivate_single_success"].format(channel=channel_label)
        toast_tone = "success"
    else:
        reason = _format_reactivate_failure_reason(txt, reason_code)
        logger.warning(
            "event=channels.reactivate_single_failed channel_id=%s channel_name=%s reason_code=%s",
            normalized,
            channel_label,
            reason_code,
            extra={"event": "channels.reactivate_single_failed", "code": reason_code},
        )
        toast_message = txt["channel_reactivate_single_failure"].format(
            channel=channel_label,
            reason=reason,
        )
        toast_tone = "error"

    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request,
        requested_status,
    )
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        **_channel_management_ui_context(request),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context=context,
        headers=_reactivate_toast_header(toast_message, toast_tone),
    )


@router.get("/video-list")
async def video_list(
    request: Request,
    channel_id: str | None = Query(default=None),
    category_id: int | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    if limit is None:
        limit = await repository.get_videos_per_page_setting(request.app.state.runtime.db)

    total = await repository.count_videos(
        request.app.state.runtime.db, channel_id=channel_id, category_id=category_id,
    )
    total_pages = max(1, (total + limit - 1) // limit)
    current_page = min(max(1, page), total_pages)
    videos = await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=current_page,
        limit=limit,
        category_id=category_id,
    )
    channels = await repository.list_channels(request.app.state.runtime.db)
    categories = await repository.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        videos=videos,
        channels=channels,
        categories_for_filter=categories,
        pagination={
            "page": current_page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "channel_id": channel_id or "",
            "category_id": category_id if category_id is not None else "",
            "sort": sort,
            "order": order,
        },
    )
    return request.app.state.templates.TemplateResponse(request=request, name="fragments/video_list.html", context=context)


@router.post("/videos/delete-selected")
async def delete_selected_videos(request: Request):
    form = await request.form()
    video_ids = [str(v).strip() for v in form.getlist("video_id") if str(v).strip()]

    if video_ids:
        result = await repository.delete_videos_by_ids(
            request.app.state.runtime.db, video_ids,
        )
        _cleanup_thumbnail_files(
            result["thumbnail_paths"],
            request.app.state.runtime.config.thumbnail_dir,
        )

    page = _safe_int(form.get("_page"), 1)
    limit_val = _safe_int(form.get("_limit"), 0)
    if limit_val <= 0:
        limit_val = await repository.get_videos_per_page_setting(request.app.state.runtime.db)
    sort = str(form.get("_sort") or "upload_time")
    order = str(form.get("_order") or "desc")
    channel_id = str(form.get("_channel_id") or "") or None
    raw_cat = form.get("_category_id")
    category_id = int(raw_cat) if raw_cat and str(raw_cat).strip().isdigit() else None

    total = await repository.count_videos(
        request.app.state.runtime.db, channel_id=channel_id, category_id=category_id,
    )
    total_pages = max(1, (total + limit_val - 1) // limit_val)
    current_page = min(max(1, page), total_pages)
    videos = await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=current_page,
        limit=limit_val,
        category_id=category_id,
    )
    channels = await repository.list_channels(request.app.state.runtime.db)
    categories = await repository.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        videos=videos,
        channels=channels,
        categories_for_filter=categories,
        pagination={
            "page": current_page,
            "limit": limit_val,
            "total": total,
            "total_pages": total_pages,
            "channel_id": channel_id or "",
            "category_id": category_id if category_id is not None else "",
            "sort": sort,
            "order": order,
        },
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_list.html",
        context=context,
    )


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
        defaults = await repository.get_download_default_settings(
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
                limit_val = await repository.get_videos_per_page_setting(request.app.state.runtime.db)
            sort = str(form.get("_sort") or "upload_time")
            order = str(form.get("_order") or "desc")
            channel_id = str(form.get("_channel_id") or "") or None
            raw_cat_early = form.get("_category_id")
            category_id_early = int(raw_cat_early) if raw_cat_early and str(raw_cat_early).strip().isdigit() else None

            total = await repository.count_videos(
                request.app.state.runtime.db, channel_id=channel_id, category_id=category_id_early,
            )
            total_pages = max(1, (total + limit_val - 1) // limit_val)
            current_page = min(max(1, page), total_pages)
            videos = await repository.list_videos(
                request.app.state.runtime.db,
                channel_id=channel_id,
                sort=sort,
                order=order,
                page=current_page,
                limit=limit_val,
                category_id=category_id_early,
            )
            channels = await repository.list_channels(request.app.state.runtime.db)
            categories_early = await repository.list_categories(request.app.state.runtime.db)
            context = await build_template_context(
                request,
                videos=videos,
                channels=channels,
                categories_for_filter=categories_early,
                pagination={
                    "page": current_page,
                    "limit": limit_val,
                    "total": total,
                    "total_pages": total_pages,
                    "channel_id": channel_id or "",
                    "category_id": category_id_early if category_id_early is not None else "",
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
        limit_val = await repository.get_videos_per_page_setting(request.app.state.runtime.db)
    sort = str(form.get("_sort") or "upload_time")
    order = str(form.get("_order") or "desc")
    channel_id = str(form.get("_channel_id") or "") or None
    raw_cat_final = form.get("_category_id")
    category_id_final = int(raw_cat_final) if raw_cat_final and str(raw_cat_final).strip().isdigit() else None

    total = await repository.count_videos(
        request.app.state.runtime.db, channel_id=channel_id, category_id=category_id_final,
    )
    total_pages = max(1, (total + limit_val - 1) // limit_val)
    current_page = min(max(1, page), total_pages)
    videos = await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=current_page,
        limit=limit_val,
        category_id=category_id_final,
    )
    channels = await repository.list_channels(request.app.state.runtime.db)
    categories_final = await repository.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        videos=videos,
        channels=channels,
        categories_for_filter=categories_final,
        pagination={
            "page": current_page,
            "limit": limit_val,
            "total": total,
            "total_pages": total_pages,
            "channel_id": channel_id or "",
            "category_id": category_id_final if category_id_final is not None else "",
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


@router.get("/video-detail/{video_id}")
async def video_detail(video_id: str, request: Request):
    detail = await repository.get_video_detail(request.app.state.runtime.db, video_id)
    if detail:
        await repository.mark_video_viewed(request.app.state.runtime.db, video_id)
    download_defaults = await repository.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    context = await build_template_context(
        request,
        video=detail,
        download_defaults=download_defaults,
        ffmpeg_available=is_ffmpeg_available(),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_detail.html",
        context=context,
    )


@router.get("/search-results")
async def search_results(request: Request, q: str = Query(default="")):
    results = await repository.search_documents(request.app.state.runtime.db, q) if q else []
    context = await build_template_context(request, results=results, q=q)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/search_results.html",
        context=context,
    )


@router.get("/status-badge/{video_id}")
async def status_badge(video_id: str, request: Request):
    video = await repository.get_video(request.app.state.runtime.db, video_id)
    status = video["pipeline_status"] if video else "unknown"
    context = await build_template_context(request, status=status)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/status_badge.html",
        context=context,
    )


@router.post("/channels/bulk-resolve")
async def bulk_resolve(request: Request):
    form = await request.form()
    channel_status = repository.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    bulk_text = str(form.get("bulk_text", ""))
    upload = form.get("takeout_file")
    takeout_data = parse_takeout_entries("takeout.txt", b"")
    if isinstance(upload, UploadFile):
        data = await upload.read()
        takeout_data = parse_takeout_entries(upload.filename or "takeout.txt", data)

    collected = collect_inputs_from_sources(
        bulk_text=bulk_text,
        takeout_data=takeout_data,
    )
    result = await resolve_bulk_inputs(
        inputs=collected["inputs"],
        direct_channels=collected["direct_channels"],
        resolver=request.app.state.runtime.channel_resolver,
    )
    context = await build_template_context(
        request,
        result=result,
        channel_status=channel_status,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/bulk_resolve_result.html",
        context=context,
    )


@router.post("/channels/bulk-commit")
async def bulk_commit(request: Request):
    form = await request.form()
    requested_status = repository.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    items: list[dict[str, str]] = []

    resolved_ids = list(form.getlist("resolved_channel_id"))
    resolved_names = list(form.getlist("resolved_channel_name"))
    for channel_id, channel_name in zip(resolved_ids, resolved_names):
        channel_id = str(channel_id).strip()
        channel_name = str(channel_name).strip()
        if channel_id and channel_name:
            items.append({"channel_id": channel_id, "channel_name": channel_name})

    for key in form.keys():
        if not key.startswith("candidate_select_"):
            continue
        packed = str(form.get(key, ""))
        if "|||" not in packed:
            continue
        channel_id, channel_name = packed.split("|||", 1)
        channel_id = channel_id.strip()
        channel_name = channel_name.strip()
        if channel_id and channel_name:
            items.append({"channel_id": channel_id, "channel_name": channel_name})

    seen: set[str] = set()
    saved = 0
    for item in items:
        channel_id = item["channel_id"]
        channel_name = item["channel_name"]
        if channel_id in seen:
            continue
        seen.add(channel_id)
        await repository.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        saved += 1

    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request,
        requested_status,
    )
    context = await build_template_context(
        request,
        saved=saved,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        **_channel_management_ui_context(request),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/bulk_commit_result.html",
        context=context,
    )


@router.get("/settings/llm/runtime-status")
async def llm_runtime_status_fragment(request: Request):
    context = await build_template_context(
        request,
        include_llm_runtime_status=True,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/llm_runtime_status.html",
        context=context,
    )


@router.post("/settings/llm/resume")
async def resume_llm_runtime(request: Request):
    context = await build_template_context(
        request,
        include_llm_runtime_status=True,
    )
    txt = context["txt"]
    llm_runtime_status = context["llm_runtime_status"]
    if isinstance(txt, dict) and isinstance(llm_runtime_status, dict):
        status = LlmRuntimeStatus(
            ready=bool(llm_runtime_status.get("ready")),
            code=str(llm_runtime_status.get("code") or ""),
            reason=str(llm_runtime_status.get("reason") or ""),
            providers_to_try=list(llm_runtime_status.get("providers_to_try") or []),
            warnings=list(llm_runtime_status.get("warnings") or []),
            pending_count=int(llm_runtime_status.get("pending_count") or 0),
        )
        if is_runtime_ready_for_resume(status):
            pending_count = int(llm_runtime_status.get("pending_count") or 0)
            if pending_count > 0:
                request.app.state.runtime.llm_wake_event.set()
                message = txt["settings_llm_runtime_resume_requested_toast"].format(count=pending_count)
                tone = "success"
            else:
                message = txt["settings_llm_runtime_resume_no_pending_toast"]
                tone = "info"
        else:
            reason = str(llm_runtime_status.get("reason_text") or txt["settings_llm_runtime_reason_generic"])
            message = txt["settings_llm_runtime_resume_blocked_toast"].format(reason=reason)
            tone = "error"
    else:
        message = "LLM runtime status is unavailable"
        tone = "error"

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/llm_runtime_status.html",
        context=context,
        headers=_llm_runtime_toast_header(message, tone),
    )


@router.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: int,
    request: Request,
    confirmed: str = Form(default=""),
):
    if confirmed != "on":
        return Response(status_code=400)

    affected = await repository.acknowledge_alert(
        request.app.state.runtime.db,
        alert_id=alert_id,
    )
    if affected == 0:
        return Response(status_code=404)
    return Response(status_code=200)


@router.post("/alerts/ack-group")
async def acknowledge_alert_group(
    request: Request,
    alert_type: str = Form(default=""),
    confirmed: str = Form(default=""),
):
    if confirmed != "on":
        return Response(status_code=400)

    normalized_alert_type = str(alert_type).strip()
    if not normalized_alert_type:
        return Response(status_code=400)

    affected = await repository.acknowledge_alerts_by_type(
        request.app.state.runtime.db,
        alert_type=normalized_alert_type,
    )
    if affected == 0:
        return Response(status_code=404)
    return Response(status_code=200)
