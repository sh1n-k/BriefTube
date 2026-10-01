"""JSON API endpoints, composed from domain submodules."""

from __future__ import annotations

from fastapi import APIRouter

from app.routers import (
    api_categories,
    api_channels,
    api_downloads,
    api_queue,
    api_settings,
    api_videos,
)

router = APIRouter(prefix="/api", tags=["api"])
router.include_router(api_downloads.router)
router.include_router(api_categories.router)
router.include_router(api_channels.router)
router.include_router(api_queue.router)
router.include_router(api_videos.router)
router.include_router(api_settings.router)
