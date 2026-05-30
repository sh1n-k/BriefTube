from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

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


@router.get("/videos/{video_id}/article-preview-modal")
async def video_article_preview_modal(video_id: str, request: Request):
    context = await build_video_detail_dynamic_context(
        request,
        video_id=video_id,
    )
    video = context.get("video")
    if not isinstance(video, dict) or not str(video.get("article_title") or "").strip():
        raise HTTPException(status_code=404, detail="Article not found")
    context["article_preview_from_list"] = True
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/article_preview_modal.html",
        context=context,
    )
