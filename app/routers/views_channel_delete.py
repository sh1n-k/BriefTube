from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.domains.channels import delete_channels_and_cleanup
from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.routers.helpers import parse_optional_int
from app.routers.template_context import build_template_context
from app.routers.views_common import (
    _channel_management_ui_context,
    _resolve_channel_management_state,
)

router = APIRouter(tags=["views"])


@router.post("/channels/delete-selected")
async def delete_selected_channels(request: Request):
    form = await request.form()
    requested_status = channels_repo.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    requested_category_id = parse_optional_int(
        str(form.get("category_id") or request.query_params.get("category_id", ""))
    )
    channel_ids = [str(value).strip() for value in form.getlist("channel_id") if str(value).strip()]
    await delete_channels_and_cleanup(request.app.state.runtime, channel_ids)

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
    )


@router.post("/channels/{channel_id}/delete")
async def delete_single_channel(
    channel_id: str,
    request: Request,
    status: str = Query(default=channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
    category_id: int | None = Query(default=None),
):
    normalized = channel_id.strip()
    requested_status = channels_repo.normalize_channel_management_status(status)
    await delete_channels_and_cleanup(request.app.state.runtime, [normalized])

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
    )
