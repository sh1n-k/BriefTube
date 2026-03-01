from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.repositories import downloads as downloads_repo
from app.routers.template_context import build_template_context
from app.services.downloads import is_ffmpeg_available

router = APIRouter(tags=["pages"])
DOWNLOAD_PAGE_LIMIT = 50


@router.get("/downloads")
async def downloads_page(
    request: Request,
    status: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
):
    normalized_status = downloads_repo.normalize_download_status_filter(status)
    jobs = await downloads_repo.list_download_jobs(
        request.app.state.runtime.db,
        status=normalized_status,
        page=page,
        limit=DOWNLOAD_PAGE_LIMIT,
    )
    total = await downloads_repo.count_download_jobs(
        request.app.state.runtime.db,
        status=normalized_status,
    )
    counts = await downloads_repo.count_download_jobs_by_status(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        jobs=jobs,
        download_status=normalized_status,
        download_total=total,
        download_page=page,
        download_limit=DOWNLOAD_PAGE_LIMIT,
        download_counts=counts,
        ffmpeg_available=is_ffmpeg_available(),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="downloads.html",
        context=context,
    )
