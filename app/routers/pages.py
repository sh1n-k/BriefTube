from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

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


@router.get("/settings")
async def settings_page(request: Request):
    guard = await repository.get_transcript_guard_state(request.app.state.runtime.db)
    reset_done = request.query_params.get("guard_reset") == "1"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"transcript_guard": guard, "guard_reset_done": reset_done},
    )


@router.post("/settings/transcript-guard/reset")
async def settings_reset_transcript_guard(request: Request):
    form = await request.form()
    confirmed = str(form.get("confirm_guard_reset", "")).strip().lower()
    if confirmed != "on":
        return RedirectResponse(url="/settings?guard_reset=0", status_code=303)

    await repository.reset_transcript_guard_state(request.app.state.runtime.db)
    return RedirectResponse(url="/settings?guard_reset=1", status_code=303)
