from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.pipeline_status import MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES
from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.routers.helpers import parse_optional_int
from app.routers.template_context import build_template_context


@dataclass(frozen=True, slots=True)
class VideoListContext:
    context: dict[str, Any]
    page: int
    limit: int
    category_id: int | None
    pipeline_status: str | None


async def build_video_list_context(
    request: Request,
    *,
    q: str = "",
    channel_id: str | None,
    category_id: object,
    pipeline_status: str | None,
    sort: str,
    order: str,
    page: int,
    limit: int | None,
) -> VideoListContext:
    normalized_category_id = parse_optional_int(category_id)
    normalized_pipeline_status = videos_repo.normalize_pipeline_status_filter(pipeline_status)
    resolved_limit = limit or await settings_repo.get_videos_per_page_setting(
        request.app.state.runtime.db
    )
    total = await videos_repo.count_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        category_id=normalized_category_id,
        pipeline_status=normalized_pipeline_status,
    )
    total_pages = max(1, (total + resolved_limit - 1) // resolved_limit)
    current_page = min(max(1, page), total_pages)
    videos = await videos_repo.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=current_page,
        limit=resolved_limit,
        category_id=normalized_category_id,
        pipeline_status=normalized_pipeline_status,
    )
    all_channels = await channels_repo.list_channels(request.app.state.runtime.db)
    channels = (
        [
            channel
            for channel in all_channels
            if channel.get("category_id") == normalized_category_id
        ]
        if normalized_category_id is not None
        else all_channels
    )
    categories = await categories_repo.list_categories(request.app.state.runtime.db)
    search_results = (
        await videos_repo.search_documents(request.app.state.runtime.db, q) if q else []
    )
    context = await build_template_context(
        request,
        channels=channels,
        videos=videos,
        q=q,
        results=search_results,
        categories_for_filter=categories,
        status_filter_options=videos_repo.VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES,
        article_enqueue_skip_statuses=MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES,
        pagination={
            "page": current_page,
            "limit": resolved_limit,
            "total": total,
            "total_pages": total_pages,
            "channel_id": channel_id or "",
            "category_id": normalized_category_id if normalized_category_id is not None else "",
            "pipeline_status": normalized_pipeline_status or "",
            "sort": sort,
            "order": order,
        },
    )
    return VideoListContext(
        context=context,
        page=current_page,
        limit=resolved_limit,
        category_id=normalized_category_id,
        pipeline_status=normalized_pipeline_status,
    )
