"""Full-page router composition.

Feature routes live in the adjacent ``pages_*`` modules so a page change does
not require loading every full-page handler.
"""

from fastapi import APIRouter

from app.routers import (
    pages_channels,
    pages_downloads,
    pages_home,
    pages_retention,
    pages_settings,
)

router = APIRouter(tags=["pages"])
router.include_router(pages_home.router)
router.include_router(pages_channels.router)
router.include_router(pages_settings.router)
router.include_router(pages_retention.router)
router.include_router(pages_downloads.router)
