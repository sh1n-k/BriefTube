from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Query, Request
from starlette.datastructures import UploadFile

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.repositories import settings as settings_repo
from app.routers.helpers import (
    cleanup_thumbnail_files,
    htmx_trigger_header,
    parse_optional_int,
    request_texts,
)
from app.routers.template_context import build_template_context
from app.routers.views_common import (
    REACTIVATE_BATCH_LIMIT,
    _channel_management_ui_context,
    _resolve_channel_management_state,
)
from app.services.bulk_channels import (
    collect_inputs_from_sources,
    parse_takeout_entries,
    resolve_bulk_inputs,
)
from app.services.rss import RSSParseError

router = APIRouter(tags=["views"])
logger = logging.getLogger("app.routers.views")


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


def _reactivate_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("channel-reactivate-toast", {"message": message, "tone": tone})


def _channel_metadata_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("channel-metadata-toast", {"message": message, "tone": tone})


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
        (
            _,
            new_etag,
            new_last_modified,
        ) = await request.app.state.runtime.rss_service.fetch_channel_feed(
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
    except RSSParseError:
        logger.debug(
            "event=channels.reactivate_probe_failed channel_id=%s reason=parse_error",
            channel_id,
            extra={"event": "channels.reactivate_probe_failed", "code": "rss_parse_error"},
        )
        return False, "error_rss_parse"
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
    status: str = Query(default=channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
    category_id: int | None = Query(default=None),
):
    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request,
        status,
        category_id=category_id,
    )
    categories = await categories_repo.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        categories=categories,
        selected_category_id=category_id,
        **_channel_management_ui_context(request, channel_counts),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list_result.html",
        context=context,
    )


@router.post("/channels/add")
async def add_channel(request: Request):
    form = await request.form()
    requested_status = channels_repo.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    requested_category_id = parse_optional_int(
        str(form.get("category_id") or request.query_params.get("category_id", ""))
    )
    selected_candidate = str(form.get("selected_candidate", "")).strip()
    source = str(form.get("source", "")).strip()
    txt = await request_texts(request)

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
                add_category_id=requested_category_id,
            )
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="fragments/channel_add_result.html",
                context=context,
            )

        channel_id, channel_name = unpacked
        await channels_repo.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        await channels_repo.enqueue_channel_metadata_refresh(
            request.app.state.runtime.db,
            channel_id=channel_id,
        )
        request.app.state.runtime.channel_metadata_wake_event.set()
        channel_status, channels, channel_counts = await _resolve_channel_management_state(
            request,
            requested_status,
            category_id=requested_category_id,
        )
        categories = await categories_repo.list_categories(request.app.state.runtime.db)
        context = await build_template_context(
            request,
            add_mode="success",
            add_message=txt["channel_add_saved"],
            add_source="",
            add_candidates=[],
            channels=channels,
            channel_status=channel_status,
            channel_counts=channel_counts,
            categories=categories,
            selected_category_id=requested_category_id,
            add_status=channel_status,
            add_category_id=requested_category_id,
            **_channel_management_ui_context(request, channel_counts),
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
            add_category_id=requested_category_id,
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
            add_category_id=requested_category_id,
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
            await channels_repo.add_channel(
                request.app.state.runtime.db,
                channel_id=channel_id,
                channel_name=channel_name,
                channel_handle=str(item.get("channel_handle", "")).strip() or None,
                channel_url_canonical=str(item.get("channel_url", "")).strip() or None,
                channel_thumbnail_url=str(item.get("channel_thumbnail_url", "")).strip() or None,
                channel_description=str(item.get("channel_description", "")).strip() or None,
                channel_language_hint=str(item.get("channel_language_hint", "")).strip() or None,
                metadata_fetch_status=channels_repo.CHANNEL_METADATA_STATUS_PENDING,
            )
            await channels_repo.enqueue_channel_metadata_refresh(
                request.app.state.runtime.db,
                channel_id=channel_id,
            )
            request.app.state.runtime.channel_metadata_wake_event.set()
            channel_status, channels, channel_counts = await _resolve_channel_management_state(
                request,
                requested_status,
                category_id=requested_category_id,
            )
            categories = await categories_repo.list_categories(request.app.state.runtime.db)
            context = await build_template_context(
                request,
                add_mode="success",
                add_message=txt["channel_add_saved"],
                add_source="",
                add_candidates=[],
                channels=channels,
                channel_status=channel_status,
                channel_counts=channel_counts,
                categories=categories,
                selected_category_id=requested_category_id,
                add_status=channel_status,
                add_category_id=requested_category_id,
                **_channel_management_ui_context(request, channel_counts),
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
            add_category_id=requested_category_id,
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
        add_category_id=requested_category_id,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_add_result.html",
        context=context,
    )


@router.post("/channels/delete-selected")
async def delete_selected_channels(request: Request):
    form = await request.form()
    requested_status = channels_repo.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    channel_ids = [str(value).strip() for value in form.getlist("channel_id") if str(value).strip()]
    result = await channels_repo.delete_channels_with_related_data(
        request.app.state.runtime.db,
        channel_ids,
    )
    if int(result.get("deleted_videos", 0) or 0) > 0:
        request.app.state.runtime.invalidate_retention_notice_cache()
    cleanup_thumbnail_files(
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
        **_channel_management_ui_context(request, channel_counts),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list_result.html",
        context=context,
    )


@router.post("/channels/metadata/retry-failed")
async def retry_failed_channel_metadata(request: Request):
    form = await request.form()
    requested_status = channels_repo.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    raw_category_id = str(
        form.get("category_id") or request.query_params.get("category_id", "")
    ).strip()
    selected_category_id = int(raw_category_id) if raw_category_id.isdigit() else None
    queued = await channels_repo.enqueue_failed_channel_metadata(
        request.app.state.runtime.db,
        status=requested_status,
        category_id=selected_category_id,
    )
    if queued > 0:
        request.app.state.runtime.channel_metadata_wake_event.set()
    logger.info(
        "event=channels.metadata.retry_queued status=%s category_id=%s queued=%s",
        requested_status,
        selected_category_id if selected_category_id is not None else "all",
        queued,
        extra={"event": "channels.metadata.retry_queued"},
    )
    txt = await request_texts(request)
    toast_tone = "success" if queued > 0 else "info"
    toast_message = (
        txt["channel_metadata_retry_queued"].format(count=queued)
        if queued > 0
        else txt["channel_metadata_retry_none"]
    )
    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request,
        requested_status,
        category_id=selected_category_id,
    )
    categories = await categories_repo.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        categories=categories,
        selected_category_id=selected_category_id,
        **_channel_management_ui_context(request, channel_counts),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list_result.html",
        context=context,
        headers=_channel_metadata_toast_header(toast_message, toast_tone),
    )


@router.post("/channels/{channel_id}/delete")
async def delete_single_channel(
    channel_id: str,
    request: Request,
    status: str = Query(default=channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
):
    normalized = channel_id.strip()
    requested_status = channels_repo.normalize_channel_management_status(status)
    result = await channels_repo.delete_channels_with_related_data(
        request.app.state.runtime.db,
        [normalized],
    )
    if int(result.get("deleted_videos", 0) or 0) > 0:
        request.app.state.runtime.invalidate_retention_notice_cache()
    cleanup_thumbnail_files(
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
        **_channel_management_ui_context(request, channel_counts),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list_result.html",
        context=context,
    )


@router.post("/channels/reactivate-selected")
async def reactivate_selected_channels(request: Request):
    form = await request.form()
    requested_status = channels_repo.normalize_channel_management_status(
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
        result = await channels_repo.delete_channels_with_related_data(
            request.app.state.runtime.db,
            channel_ids,
        )
        if int(result.get("deleted_videos", 0) or 0) > 0:
            request.app.state.runtime.invalidate_retention_notice_cache()
        cleanup_thumbnail_files(
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
        txt = await request_texts(request)
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
                extra={
                    "event": "channels.reactivate_bulk_limited",
                    "code": str(REACTIVATE_BATCH_LIMIT),
                },
            )
        else:
            policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
            feed_mode = str(policy.get("rss_feed_mode", settings_repo.RSS_FEED_MODE_DEFAULT))
            channel_name_map = await channels_repo.get_channel_name_map(
                request.app.state.runtime.db,
                channel_ids,
            )
            success_ids: list[str] = []
            failed: list[tuple[str, str]] = []
            probe_delay_seconds = min(
                0.5,
                max(0.0, float(request.app.state.runtime.config.rss_inter_channel_delay_seconds)),
            )
            for index, channel_id in enumerate(channel_ids):
                if index > 0 and probe_delay_seconds > 0:
                    await asyncio.sleep(probe_delay_seconds)
                ok, reason_code = await _probe_channel_reactivation(
                    request,
                    channel_id,
                    feed_mode,
                )
                if ok:
                    success_ids.append(channel_id)
                else:
                    failed.append((channel_id, reason_code))

            await channels_repo.reactivate_channels(request.app.state.runtime.db, success_ids)

            success_count = len(success_ids)
            failed_labels = [
                channel_name_map.get(channel_id, channel_id) for channel_id, _ in failed
            ]
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
        **_channel_management_ui_context(request, channel_counts),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list_result.html",
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
    status: str = Query(default=channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
):
    normalized = channel_id.strip()
    requested_status = channels_repo.normalize_channel_management_status(status)
    logger.debug(
        "event=channels.reactivate_single_requested channel_id=%s status=%s",
        normalized,
        requested_status,
        extra={"event": "channels.reactivate_single_requested"},
    )
    txt = await request_texts(request)
    channel_name_map = await channels_repo.get_channel_name_map(
        request.app.state.runtime.db,
        [normalized],
    )
    channel_label = channel_name_map.get(normalized, normalized)
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    feed_mode = str(policy.get("rss_feed_mode", settings_repo.RSS_FEED_MODE_DEFAULT))
    ok, reason_code = await _probe_channel_reactivation(request, normalized, feed_mode)
    if ok:
        await channels_repo.reactivate_channel(request.app.state.runtime.db, normalized)
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
        **_channel_management_ui_context(request, channel_counts),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list_result.html",
        context=context,
        headers=_reactivate_toast_header(toast_message, toast_tone),
    )


@router.post("/channels/bulk-resolve")
async def bulk_resolve(request: Request):
    form = await request.form()
    channel_status = channels_repo.normalize_channel_management_status(
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
    requested_status = channels_repo.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    requested_category_id = parse_optional_int(
        str(form.get("category_id") or request.query_params.get("category_id", ""))
    )
    items: list[dict[str, str]] = []

    resolved_ids = list(form.getlist("resolved_channel_id"))
    resolved_names = list(form.getlist("resolved_channel_name"))
    for channel_id, channel_name in zip(resolved_ids, resolved_names, strict=False):
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
        await channels_repo.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        await channels_repo.enqueue_channel_metadata_refresh(
            request.app.state.runtime.db,
            channel_id=channel_id,
        )
        saved += 1
    if saved > 0:
        request.app.state.runtime.channel_metadata_wake_event.set()

    channel_status, channels, channel_counts = await _resolve_channel_management_state(
        request,
        requested_status,
        category_id=requested_category_id,
    )
    categories = await categories_repo.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        saved=saved,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        categories=categories,
        selected_category_id=requested_category_id,
        **_channel_management_ui_context(request, channel_counts),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/bulk_commit_result.html",
        context=context,
    )
