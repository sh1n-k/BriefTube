from __future__ import annotations

from fastapi import APIRouter

from app.routers import (
    views_alerts,
    views_categories,
    views_channel_add,
    views_channel_bulk,
    views_channel_delete,
    views_channel_metadata,
    views_channels,
    views_downloads,
    views_llm,
    views_video_detail_fragments,
    views_videos,
)

router = APIRouter(prefix="/views", tags=["views"])
router.include_router(views_downloads.router)
router.include_router(views_categories.router)
router.include_router(views_channel_add.router)
router.include_router(views_channel_bulk.router)
router.include_router(views_channel_delete.router)
router.include_router(views_channel_metadata.router)
router.include_router(views_channels.router)
router.include_router(views_video_detail_fragments.router)
router.include_router(views_videos.router)
router.include_router(views_llm.router)
router.include_router(views_alerts.router)
