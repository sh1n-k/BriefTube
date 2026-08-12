from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from app.repositories import transcripts as transcripts_repo
from app.routers.video_detail_context import build_video_detail_context
from app.routers.video_list_context import build_video_list_context

router = APIRouter(tags=["pages"])


@router.get("/")
async def home(
    request: Request,
    q: str = Query(default=""),
    channel_id: str | None = Query(default=None),
    category_id: str | None = Query(default=None),
    pipeline_status: str | None = Query(default=None),
    viewed: str | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    result = await build_video_list_context(
        request,
        q=q,
        channel_id=channel_id,
        category_id=category_id,
        pipeline_status=pipeline_status,
        viewed=viewed,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
    )
    return request.app.state.templates.TemplateResponse(
        request=request, name="index.html", context=result.context
    )


@router.get("/videos/{video_id}")
async def video_page(video_id: str, request: Request):
    context = await build_video_detail_context(
        request,
        video_id=video_id,
        transcript_retry_done=request.query_params.get("transcript_retry") == "1",
        mark_viewed=True,
    )
    status_code = 200 if context.get("video") else 404
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="video_detail.html",
        context=context,
        status_code=status_code,
    )


@router.post("/videos/{video_id}/transcript/retry")
async def retry_transcript(video_id: str, request: Request):
    affected = await transcripts_repo.reset_transcript_for_retry(
        request.app.state.runtime.db, video_id
    )
    retry_flag = "1" if affected > 0 else "0"
    return RedirectResponse(
        url=f"/videos/{video_id}?transcript_retry={retry_flag}", status_code=303
    )
