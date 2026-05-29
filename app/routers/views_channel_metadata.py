from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.routers.helpers import htmx_trigger_header, request_texts
from app.routers.template_context import build_template_context
from app.routers.views_common import (
    _channel_management_ui_context,
    _resolve_channel_management_state,
)

router = APIRouter(tags=["views"])
logger = logging.getLogger("app.routers.views")


def _channel_metadata_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("channel-metadata-toast", {"message": message, "tone": tone})


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
