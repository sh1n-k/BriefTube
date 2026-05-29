from __future__ import annotations

from fastapi import APIRouter

from app.routers import (
    views_categories,
    views_channels,
    views_downloads,
    views_settings,
    views_videos,
)

router = APIRouter(prefix="/views", tags=["views"])
router.include_router(views_downloads.router)
router.include_router(views_categories.router)
router.include_router(views_channels.router)
router.include_router(views_videos.router)
router.include_router(views_settings.router)
