"""JSON API endpoints, composed from domain submodules."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.repositories import llm as llm_repo
from app.routers import (
    api_categories,
    api_channels,
    api_downloads,
    api_queue,
    api_settings,
    api_videos,
)

__all__ = ["llm_repo", "logger", "router"]

router = APIRouter(prefix="/api", tags=["api"])
router.include_router(api_downloads.router)
router.include_router(api_categories.router)
router.include_router(api_channels.router)
router.include_router(api_queue.router)
router.include_router(api_videos.router)
router.include_router(api_settings.router)
logger = logging.getLogger(__name__)
