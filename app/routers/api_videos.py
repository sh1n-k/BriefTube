"""Video and request JSON API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from app.repositories import manual_articles as manual_articles_repo
from app.repositories import manual_transcripts as manual_transcripts_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.repositories import videos as videos_repo
from app.routers.helpers import manual_transcript_requests_disabled

ARTICLE_REQUEST_BULK_LIMIT = 10

logger = logging.getLogger("app.routers.api")

router = APIRouter(tags=["api"])


@router.get("/videos")
async def get_videos(
    request: Request,
    channel_id: str | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    if limit is None:
        limit = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)

    return await videos_repo.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
    )


@router.get("/videos/{video_id}")
async def get_video(video_id: str, request: Request):
    detail = await videos_repo.get_video_detail(request.app.state.runtime.db, video_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Video not found")
    return detail


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str, request: Request):
    transcript = await videos_repo.get_transcript(request.app.state.runtime.db, video_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


@router.get("/videos/{video_id}/article")
async def get_article(video_id: str, request: Request):
    article = await videos_repo.get_article(request.app.state.runtime.db, video_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@router.get("/search")
async def search(request: Request, q: str = Query(min_length=1)):
    return await videos_repo.search_documents(request.app.state.runtime.db, query=q)


@router.post("/videos/{video_id}/retry")
async def retry_video(video_id: str, request: Request):
    affected = await videos_repo.mark_video_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Retry target not found")
    return {"ok": True, "video_id": video_id}


@router.post("/videos/{video_id}/transcript-request")
async def request_video_transcript(video_id: str, request: Request):
    if manual_transcript_requests_disabled():
        raise HTTPException(status_code=403, detail="manual transcript requests are disabled")

    result = await manual_transcripts_repo.enqueue_manual_transcript_job(
        request.app.state.runtime.db,
        video_id=video_id,
    )
    status = str(result.get("status") or "").strip().lower()
    reason = str(result.get("reason") or "").strip()
    if reason == "not_found":
        raise HTTPException(status_code=404, detail="Video not found")
    if status == "failed":
        raise HTTPException(status_code=409, detail=reason or "manual transcript request rejected")

    if status == "queued":
        request.app.state.runtime.manual_transcript_wake_event.set()

    return {
        "ok": status in {"queued", "skipped"},
        "video_id": video_id,
        "status": status,
        "job_id": result.get("job_id"),
        "reason": reason,
    }


@router.post("/videos/article-request")
async def request_videos_article(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        raise HTTPException(status_code=415, detail="application/json content-type required")

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid json payload") from exc

    raw_video_ids = payload.get("video_ids", []) if isinstance(payload, dict) else []
    if not isinstance(raw_video_ids, list):
        raise HTTPException(status_code=400, detail="video_ids must be an array")

    selected_ids = [str(video_id).strip() for video_id in raw_video_ids if str(video_id).strip()]
    video_ids = list(dict.fromkeys(selected_ids))
    if not video_ids:
        raise HTTPException(status_code=400, detail="video_ids is required")
    if len(video_ids) > ARTICLE_REQUEST_BULK_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"video_ids can include up to {ARTICLE_REQUEST_BULK_LIMIT} items",
        )

    bulk_result = await manual_articles_repo.enqueue_manual_article_jobs(
        request.app.state.runtime.db,
        video_ids=video_ids,
    )
    new_count = int(bulk_result.get("new_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    retry_count = (
        int(bulk_result.get("retry_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    )
    skip_count = int(bulk_result.get("skip_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    failed_count = (
        int(bulk_result.get("failed_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    )

    if (new_count + retry_count) > 0:
        request.app.state.runtime.manual_article_wake_event.set()

    llm_worker_waiting = not await settings_repo.is_worker_enabled(
        request.app.state.runtime.db,
        "llm",
    )

    return {
        "ok": True,
        "requested_count": len(video_ids),
        "summary": {
            "new": new_count,
            "retry": retry_count,
            "skip": skip_count,
            "failed": failed_count,
        },
        "llm_worker_waiting": llm_worker_waiting,
    }


@router.post("/videos/{video_id}/transcript/retry")
async def retry_transcript(video_id: str, request: Request):
    affected = await transcripts_repo.reset_transcript_for_retry(
        request.app.state.runtime.db, video_id
    )
    if affected == 0:
        raise HTTPException(status_code=404, detail="Transcript retry target not found")
    return {"ok": True, "video_id": video_id}
