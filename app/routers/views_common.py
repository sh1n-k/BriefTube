from __future__ import annotations

from fastapi import Request

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.routers.helpers import build_rss_poll_preview
from app.routers.template_context import build_template_context

REACTIVATE_BATCH_LIMIT = 50


async def _resolve_channel_management_state(
    request: Request,
    raw_status: str | None,
    category_id: int | None = None,
) -> tuple[str, list[dict[str, object]], dict[str, int]]:
    channel_status = channels_repo.normalize_channel_management_status(raw_status)
    channels = await channels_repo.list_channels_for_management(
        request.app.state.runtime.db,
        status=channel_status,
        category_id=category_id,
    )
    channel_counts = await channels_repo.count_channels_by_status(request.app.state.runtime.db)
    return channel_status, channels, channel_counts


def _channel_management_ui_context(
    request: Request,
    channel_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    probe_delay_seconds = min(
        0.5,
        max(0.0, float(request.app.state.runtime.config.rss_inter_channel_delay_seconds)),
    )
    return {
        "reactivate_batch_limit": REACTIVATE_BATCH_LIMIT,
        "reactivate_probe_timeout_seconds": max(
            1,
            int(request.app.state.runtime.config.rss_timeout_seconds),
        ),
        "reactivate_probe_delay_seconds": probe_delay_seconds,
        "rss_poll_preview": build_rss_poll_preview(
            config=request.app.state.runtime.config,
            channel_counts=channel_counts,
        ),
    }


async def _render_category_sidebar(
    request: Request,
    selected_category_id: int | None = None,
    channel_status: str = channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE,
    *,
    refresh_channel_list: bool = False,
    channel_list_category_id: int | None = None,
):
    normalized_status = channels_repo.normalize_channel_management_status(channel_status)
    categories = await categories_repo.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        categories=categories,
        selected_category_id=selected_category_id,
        channel_status=normalized_status,
    )
    if refresh_channel_list:
        refresh_status, channels, channel_counts = await _resolve_channel_management_state(
            request,
            normalized_status,
            category_id=channel_list_category_id,
        )
        context.update(
            channels=channels,
            channel_status=refresh_status,
            channel_counts=channel_counts,
            categories=categories,
            **_channel_management_ui_context(request, channel_counts),
        )

    template_name = (
        "fragments/category_sidebar_result.html"
        if refresh_channel_list
        else "fragments/category_sidebar.html"
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
    )
