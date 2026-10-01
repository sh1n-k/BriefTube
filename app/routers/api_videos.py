"""Video and request JSON API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.repositories import transcripts as transcripts_repo
from app.repositories import videos as videos_repo

router = APIRouter(tags=["api"])


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str, request: Request):
    transcript = await videos_repo.get_transcript(request.app.state.runtime.db, video_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


@router.post("/videos/{video_id}/retry")
async def retry_video(video_id: str, request: Request):
    affected = await videos_repo.mark_video_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Retry target not found")
    return {"ok": True, "video_id": video_id}


@router.post("/videos/{video_id}/transcript/retry")
async def retry_transcript(video_id: str, request: Request):
    affected = await transcripts_repo.reset_transcript_for_retry(
        request.app.state.runtime.db, video_id
    )
    if affected == 0:
        raise HTTPException(status_code=404, detail="Transcript retry target not found")
    return {"ok": True, "video_id": video_id}
