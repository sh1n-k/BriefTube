from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.repositories import channels as channels_repo
from app.routers.helpers import cleanup_thumbnail_files
from app.routers.template_context import build_template_context
from app.routers.views_common import (
    _channel_management_ui_context,
    _resolve_channel_management_state,
)

router = APIRouter(tags=["views"])


async def delete_channels_and_cleanup_runtime(
    request: Request,
    channel_ids: list[str],
) -> dict:
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
    return result


@router.post("/channels/delete-selected")
async def delete_selected_channels(request: Request):
    form = await request.form()
    requested_status = channels_repo.normalize_channel_management_status(
        str(form.get("status") or request.query_params.get("status", ""))
    )
    channel_ids = [str(value).strip() for value in form.getlist("channel_id") if str(value).strip()]
    await delete_channels_and_cleanup_runtime(request, channel_ids)

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


@router.post("/channels/{channel_id}/delete")
async def delete_single_channel(
    channel_id: str,
    request: Request,
    status: str = Query(default=channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
):
    normalized = channel_id.strip()
    requested_status = channels_repo.normalize_channel_management_status(status)
    await delete_channels_and_cleanup_runtime(request, [normalized])

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
