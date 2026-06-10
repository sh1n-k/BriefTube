from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Query, Request

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.repositories import settings as settings_repo
from app.routers.helpers import htmx_trigger_header, parse_optional_int, request_texts
from app.routers.template_context import build_template_context
from app.routers.views_channel_delete import delete_channels_and_cleanup_runtime
from app.routers.views_common import (
    REACTIVATE_BATCH_LIMIT,
    _channel_management_ui_context,
    _resolve_channel_management_state,
)
from app.services.rss import RSSParseError

router = APIRouter(tags=["views"])
logger = logging.getLogger("app.routers.views")


def _reactivate_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("channel-reactivate-toast", {"message": message, "tone": tone})


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


@router.post("/channels/reactivate-selected")
async def reactivate_selected_channels(request: Request):
    form = await request.form()
    requested_status = channels_repo.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    requested_category_id = parse_optional_int(
        str(form.get("category_id") or request.query_params.get("category_id", ""))
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
        result = await delete_channels_and_cleanup_runtime(request, channel_ids)
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
        category_id=requested_category_id,
    )
    categories = await categories_repo.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        categories=categories,
        selected_category_id=requested_category_id,
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
    category_id: int | None = Query(default=None),
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
        headers=_reactivate_toast_header(toast_message, toast_tone),
    )
