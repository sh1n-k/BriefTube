from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.repositories import videos as videos_repo
from app.routers.helpers import full_page_redirect_for_non_fragment_request
from app.routers.video_detail_context import (
    build_video_detail_context,
    build_video_detail_dynamic_context,
)

router = APIRouter(tags=["views"])


@router.get("/videos/{video_id}/detail-fragment")
async def video_detail_fragment(video_id: str, request: Request):
    redirect = full_page_redirect_for_non_fragment_request(request, f"/videos/{video_id}")
    if redirect is not None:
        return redirect

    context = await build_video_detail_context(
        request,
        video_id=video_id,
        transcript_retry_done=False,
        mark_viewed=False,
    )
    status_code = 200 if context.get("video") else 404
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_detail.html",
        context=context,
        status_code=status_code,
    )


@router.get("/videos/{video_id}/dynamic-fragment")
async def video_detail_dynamic_fragment(video_id: str, request: Request):
    redirect = full_page_redirect_for_non_fragment_request(request, f"/videos/{video_id}")
    if redirect is not None:
        return redirect

    context = await build_video_detail_dynamic_context(
        request,
        video_id=video_id,
    )
    status_code = 200 if context.get("video") else 404
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_detail_dynamic.html",
        context=context,
        status_code=status_code,
    )


@router.get("/videos/{video_id}/article-preview-modal")
async def video_article_preview_modal(video_id: str, request: Request):
    redirect = full_page_redirect_for_non_fragment_request(request, f"/videos/{video_id}")
    if redirect is not None:
        return redirect

    context = await build_video_detail_dynamic_context(
        request,
        video_id=video_id,
    )
    video = context.get("video")
    if not isinstance(video, dict) or not str(video.get("article_title") or "").strip():
        raise HTTPException(status_code=404, detail="Article not found")
    await videos_repo.mark_video_viewed(request.app.state.runtime.db, video_id)
    context["article_preview_from_list"] = True
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/article_preview_modal.html",
        context=context,
    )
