from fastapi import APIRouter, Query, Request

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.routers.helpers import build_rss_poll_preview
from app.routers.template_context import build_template_context

router = APIRouter(tags=["pages"])
REACTIVATE_BATCH_LIMIT = 50


@router.get("/channels")
async def channel_page(
    request: Request,
    status: str = Query(default=channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
    category_id: int | None = Query(default=None),
):
    channel_status = channels_repo.normalize_channel_management_status(status)
    channels = await channels_repo.list_channels_for_management(
        request.app.state.runtime.db,
        status=channel_status,
        category_id=category_id,
    )
    channel_counts = await channels_repo.count_channels_by_status(request.app.state.runtime.db)
    categories = await categories_repo.list_categories(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        channels=channels,
        channel_status=channel_status,
        channel_counts=channel_counts,
        categories=categories,
        selected_category_id=category_id,
        rss_poll_preview=build_rss_poll_preview(
            config=request.app.state.runtime.config,
            channel_counts=channel_counts,
        ),
        reactivate_batch_limit=REACTIVATE_BATCH_LIMIT,
        reactivate_probe_timeout_seconds=max(
            1,
            int(request.app.state.runtime.config.rss_timeout_seconds),
        ),
        reactivate_probe_delay_seconds=min(
            0.5,
            max(0.0, float(request.app.state.runtime.config.rss_inter_channel_delay_seconds)),
        ),
    )
    return request.app.state.templates.TemplateResponse(
        request=request, name="channels.html", context=context
    )
