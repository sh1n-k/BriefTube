from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app import repository

router = APIRouter(prefix="/views", tags=["views"])


@router.get("/channel-list")
async def channel_list(request: Request):
    channels = await repository.list_channels(request.app.state.runtime.db)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context={"channels": channels},
    )


@router.get("/video-list")
async def video_list(
    request: Request,
    channel_id: str | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    videos = await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_list.html",
        context={"videos": videos},
    )


@router.get("/video-detail/{video_id}")
async def video_detail(video_id: str, request: Request):
    detail = await repository.get_video_detail(request.app.state.runtime.db, video_id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_detail.html",
        context={"video": detail},
    )


@router.get("/search-results")
async def search_results(request: Request, q: str = Query(default="")):
    results = await repository.search_documents(request.app.state.runtime.db, q) if q else []
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/search_results.html",
        context={"results": results, "q": q},
    )


@router.get("/status-badge/{video_id}")
async def status_badge(video_id: str, request: Request):
    video = await repository.get_video(request.app.state.runtime.db, video_id)
    status = video["restructure_status"] if video else "unknown"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/status_badge.html",
        context={"status": status},
    )
