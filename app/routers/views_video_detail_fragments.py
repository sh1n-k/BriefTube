from __future__ import annotations

from fastapi import APIRouter, Request

from app.routers.pages import build_video_detail_context, build_video_detail_dynamic_context

router = APIRouter(tags=["views"])


@router.get("/videos/{video_id}/detail-fragment")
async def video_detail_fragment(video_id: str, request: Request):
    context = await build_video_detail_context(
        request,
        video_id=video_id,
        transcript_retry_done=False,
        mark_viewed=False,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_detail.html",
        context=context,
    )


@router.get("/videos/{video_id}/dynamic-fragment")
async def video_detail_dynamic_fragment(video_id: str, request: Request):
    context = await build_video_detail_dynamic_context(
        request,
        video_id=video_id,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_detail_dynamic.html",
        context=context,
    )
