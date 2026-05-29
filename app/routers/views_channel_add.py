from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.routers.helpers import parse_optional_int, request_texts
from app.routers.template_context import build_template_context
from app.routers.views_common import (
    _channel_management_ui_context,
    _resolve_channel_management_state,
)

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
