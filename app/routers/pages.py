from __future__ import annotations

from fastapi import APIRouter, Request

from app import repository

router = APIRouter(tags=["pages"])


@router.get("/")
async def home(request: Request):
    channels = await repository.list_channels(request.app.state.runtime.db)
    videos = await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=None,
        sort="upload_time",
        order="desc",
        page=1,
        limit=20,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"channels": channels, "videos": videos},
    )


@router.get("/videos/{video_id}")
async def video_page(video_id: str, request: Request):
    detail = await repository.get_video_detail(request.app.state.runtime.db, video_id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="video_detail.html",
        context={"video": detail},
    )


@router.get("/channels")
async def channel_page(request: Request):
    channels = await repository.list_channels(request.app.state.runtime.db)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="channels.html",
        context={"channels": channels},
    )
