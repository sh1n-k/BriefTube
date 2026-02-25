from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app import repository

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/channels")
async def get_channels(request: Request):
    return await repository.list_channels(request.app.state.runtime.db)


@router.post("/channels")
async def create_channel(request: Request):
    channel_id = ""
    channel_name = ""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        channel_id = str(payload.get("channel_id", "")).strip()
        channel_name = str(payload.get("channel_name", "")).strip()
    else:
        form = await request.form()
        channel_id = str(form.get("channel_id", "")).strip()
        channel_name = str(form.get("channel_name", "")).strip()

    if not channel_id or not channel_name:
        raise HTTPException(status_code=400, detail="channel_id and channel_name are required")

    return await repository.add_channel(
        request.app.state.runtime.db,
        channel_id=channel_id,
        channel_name=channel_name,
    )


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str, request: Request):
    affected = await repository.deactivate_channel(request.app.state.runtime.db, channel_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"ok": True, "channel_id": channel_id}


@router.get("/videos")
async def get_videos(
    request: Request,
    channel_id: str | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    return await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
    )


@router.get("/videos/{video_id}")
async def get_video(video_id: str, request: Request):
    detail = await repository.get_video_detail(request.app.state.runtime.db, video_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Video not found")
    return detail


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str, request: Request):
    transcript = await repository.get_transcript(request.app.state.runtime.db, video_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


@router.get("/videos/{video_id}/article")
async def get_article(video_id: str, request: Request):
    article = await repository.get_article(request.app.state.runtime.db, video_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@router.get("/search")
async def search(request: Request, q: str = Query(min_length=1)):
    return await repository.search_documents(request.app.state.runtime.db, query=q)


@router.post("/poll/trigger")
async def trigger_poll(request: Request):
    request.app.state.runtime.poll_now_event.set()
    return {"ok": True, "triggered": True}


@router.get("/status")
async def status(request: Request):
    return await repository.queue_status(request.app.state.runtime.db)


@router.post("/videos/{video_id}/retry")
async def retry_video(video_id: str, request: Request):
    affected = await repository.mark_video_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Retry target not found")
    return {"ok": True, "video_id": video_id}


@router.get("/settings")
async def get_settings(request: Request):
    guard = await repository.get_transcript_guard_state(request.app.state.runtime.db)
    return {
        "transcript_guard": guard,
    }


@router.post("/settings/transcript-guard/reset")
async def reset_transcript_guard(request: Request):
    guard = await repository.reset_transcript_guard_state(request.app.state.runtime.db)
    return {
        "ok": True,
        "transcript_guard": guard,
    }
