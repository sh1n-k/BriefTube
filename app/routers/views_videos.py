from __future__ import annotations

import os
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request

from app.repositories import manual_articles as manual_articles_repo
from app.repositories import manual_transcripts as manual_transcripts_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.routers.helpers import (
    full_page_redirect_for_non_fragment_request,
    htmx_trigger_header,
    parse_optional_int,
    request_texts,
    safe_int,
)
from app.routers.template_context import build_template_context
from app.routers.video_detail_context import build_video_detail_context
from app.routers.video_list_context import build_video_list_context
from app.services.thumbnail_files import cleanup_thumbnail_files

router = APIRouter(tags=["views"])

ARTICLE_REQUEST_BULK_LIMIT = 10


def _video_list_push_url(
    *,
    page: int,
    limit: int,
    sort: str,
    order: str,
    channel_id: str | None,
    category_id: int | None,
    pipeline_status: str | None,
) -> str:
    params: dict[str, str] = {
        "page": str(max(1, int(page))),
        "limit": str(max(1, int(limit))),
        "sort": sort,
        "order": order,
    }
    if channel_id:
        params["channel_id"] = channel_id
    if category_id is not None:
        params["category_id"] = str(category_id)
    if pipeline_status:
        params["pipeline_status"] = pipeline_status
    return "/?" + urlencode(params)


def _page_url_with_request_query(path: str, request: Request) -> str:
    query = request.url.query
    return f"{path}?{query}" if query else path


def _video_article_request_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("video-article-request-toast", {"message": message, "tone": tone})


def _video_transcript_request_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("video-transcript-request-toast", {"message": message, "tone": tone})


def _manual_transcript_requests_disabled() -> bool:
    disabled = str(os.getenv("BRIEFTUBE_DISABLE_MANUAL_TRANSCRIPT_REQUESTS", "")).strip().lower()
    return disabled in {"1", "true", "yes", "on"}


def _manual_transcript_toast_from_result(
    txt: dict[str, str], result: dict[str, object]
) -> tuple[str, str]:
    status = str(result.get("status") or "").strip().lower()
    reason = str(result.get("reason") or "").strip().lower()
    if status == "queued":
        return txt["video_transcript_request_queued"], "success"
    if reason == "active_job_exists":
        return txt["video_transcript_request_duplicate"], "info"
    if reason == "has_transcript":
        return txt["video_transcript_request_has_transcript"], "info"
    if reason == "not_found":
        return txt["video_transcript_request_not_found"], "error"
    if reason.startswith("pipeline_status:"):
        return txt["video_transcript_request_invalid_status"], "error"
    return txt["video_transcript_request_failed"], "error"


def _resolve_article_request_toast_tone(
    *,
    new_count: int,
    retry_count: int,
    skip_count: int,
    failed_count: int,
) -> str:
    if failed_count > 0:
        return "error"
    if (new_count + retry_count) > 0:
        return "success"
    if skip_count > 0:
        return "info"
    return "error"


def _build_article_request_summary_message(
    txt: dict[str, str],
    *,
    new_count: int,
    retry_count: int,
    skip_count: int,
    failed_count: int,
    llm_worker_waiting: bool,
) -> str:
    message = txt["video_article_request_summary_toast"].format(
        new=new_count,
        retry=retry_count,
        skip=skip_count,
        failed=failed_count,
    )
    if llm_worker_waiting and (new_count + retry_count) > 0:
        message = f"{message} {txt['video_article_request_waiting_note']}"
    return message


@router.get("/video-list")
async def video_list(
    request: Request,
    channel_id: str | None = Query(default=None),
    category_id: str | None = Query(default=None),
    pipeline_status: str | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    if request.method == "GET":
        redirect = full_page_redirect_for_non_fragment_request(
            request,
            _page_url_with_request_query("/", request),
        )
        if redirect is not None:
            return redirect

    result = await build_video_list_context(
        request,
        channel_id=channel_id,
        category_id=category_id,
        pipeline_status=pipeline_status,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_list.html",
        context=result.context,
        headers={
            "HX-Push-Url": _video_list_push_url(
                page=result.page,
                limit=result.limit,
                sort=sort,
                order=order,
                channel_id=channel_id,
                category_id=result.category_id,
                pipeline_status=result.pipeline_status,
            )
        },
    )


@router.post("/videos/delete-selected")
async def delete_selected_videos(request: Request):
    form = await request.form()
    video_ids = [str(v).strip() for v in form.getlist("video_id") if str(v).strip()]

    if video_ids:
        result = await videos_repo.delete_videos_by_ids(
            request.app.state.runtime.db,
            video_ids,
        )
        if int(result.get("deleted", 0) or 0) > 0:
            request.app.state.runtime.invalidate_retention_notice_cache()
        cleanup_thumbnail_files(
            result["thumbnail_paths"],
            request.app.state.runtime.config.thumbnail_dir,
        )

    page = safe_int(form.get("_page"), 1)
    limit_val = safe_int(form.get("_limit"), 0)
    if limit_val <= 0:
        limit_val = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)
    sort = str(form.get("_sort") or "upload_time")
    order = str(form.get("_order") or "desc")
    channel_id = str(form.get("_channel_id") or "") or None
    result = await build_video_list_context(
        request,
        channel_id=channel_id,
        category_id=form.get("_category_id"),
        pipeline_status=str(form.get("_pipeline_status") or ""),
        sort=sort,
        order=order,
        page=page,
        limit=limit_val,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_list.html",
        context=result.context,
    )


@router.post("/videos/article-request-selected")
async def article_request_selected_videos(request: Request):
    form = await request.form()
    selected_ids = [str(v).strip() for v in form.getlist("video_id") if str(v).strip()]
    video_ids = list(dict.fromkeys(selected_ids))
    txt = await request_texts(request)

    llm_worker_waiting = False
    new_count = 0
    retry_count = 0
    skip_count = 0
    failed_count = 0

    if not video_ids:
        toast_message = txt["video_article_request_none_selected"]
        toast_tone = "error"
    elif len(video_ids) > ARTICLE_REQUEST_BULK_LIMIT:
        toast_message = txt["video_article_request_limit_exceeded"].format(
            selected=len(video_ids),
            limit=ARTICLE_REQUEST_BULK_LIMIT,
        )
        toast_tone = "error"
    else:
        bulk_result = await manual_articles_repo.enqueue_manual_article_jobs(
            request.app.state.runtime.db,
            video_ids=video_ids,
        )
        new_count = (
            int(bulk_result.get("new_count", 0) or 0) if isinstance(bulk_result, dict) else 0
        )
        retry_count = (
            int(bulk_result.get("retry_count", 0) or 0) if isinstance(bulk_result, dict) else 0
        )
        skip_count = (
            int(bulk_result.get("skip_count", 0) or 0) if isinstance(bulk_result, dict) else 0
        )
        failed_count = (
            int(bulk_result.get("failed_count", 0) or 0) if isinstance(bulk_result, dict) else 0
        )

        if (new_count + retry_count) > 0:
            request.app.state.runtime.manual_article_wake_event.set()

        llm_worker_waiting = not await settings_repo.is_worker_enabled(
            request.app.state.runtime.db,
            "llm",
        )

        toast_message = _build_article_request_summary_message(
            txt,
            new_count=new_count,
            retry_count=retry_count,
            skip_count=skip_count,
            failed_count=failed_count,
            llm_worker_waiting=llm_worker_waiting,
        )
        toast_tone = _resolve_article_request_toast_tone(
            new_count=new_count,
            retry_count=retry_count,
            skip_count=skip_count,
            failed_count=failed_count,
        )

    page = max(1, safe_int(form.get("_page"), 1))
    limit_val = safe_int(form.get("_limit"), 0)
    if limit_val <= 0:
        limit_val = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)
    sort = str(form.get("_sort") or "upload_time")
    order = str(form.get("_order") or "desc")
    channel_id = str(form.get("_channel_id") or "") or None
    category_id = parse_optional_int(form.get("_category_id"))
    pipeline_status = videos_repo.normalize_pipeline_status_filter(
        str(form.get("_pipeline_status") or "")
    )

    response = await video_list(
        request=request,
        channel_id=channel_id,
        category_id=str(category_id) if category_id is not None else None,
        pipeline_status=pipeline_status,
        sort=sort,
        order=order,
        page=page,
        limit=limit_val,
    )
    response.headers.update(_video_article_request_toast_header(toast_message, toast_tone))
    return response


@router.post("/videos/{video_id}/transcript-request")
async def transcript_request_single_video(video_id: str, request: Request):
    txt = await request_texts(request)
    if _manual_transcript_requests_disabled():
        response = await video_detail(video_id=video_id, request=request)
        response.status_code = 403
        response.headers.update(
            _video_transcript_request_toast_header(
                txt["video_transcript_request_forbidden"],
                "error",
            )
        )
        return response

    result = await manual_transcripts_repo.enqueue_manual_transcript_job(
        request.app.state.runtime.db,
        video_id=video_id,
    )
    if str(result.get("status") or "") == "queued":
        request.app.state.runtime.manual_transcript_wake_event.set()

    toast_message, toast_tone = _manual_transcript_toast_from_result(txt, result)
    response = await video_detail(video_id=video_id, request=request)
    response.headers.update(_video_transcript_request_toast_header(toast_message, toast_tone))
    return response


@router.post("/videos/{video_id}/article-request")
async def article_request_single_video(video_id: str, request: Request):
    txt = await request_texts(request)
    new_count = 0
    retry_count = 0
    skip_count = 0
    failed_count = 0
    detail = await videos_repo.get_video_detail(request.app.state.runtime.db, video_id)
    pipeline_status = str(detail.get("pipeline_status") or "").strip().lower() if detail else ""
    has_transcript = bool(str(detail.get("raw_text") or "").strip()) if detail else False

    if detail and pipeline_status == "done":
        if has_transcript:
            retried = await videos_repo.requeue_done_video_for_manual_article_retry(
                request.app.state.runtime.db,
                video_id,
            )
            if retried > 0:
                retry_count = 1
                request.app.state.runtime.llm_wake_event.set()
            else:
                failed_count = 1
        else:
            failed_count = 1
    else:
        bulk_result = await manual_articles_repo.enqueue_manual_article_jobs(
            request.app.state.runtime.db,
            video_ids=[video_id],
        )
        new_count = (
            int(bulk_result.get("new_count", 0) or 0) if isinstance(bulk_result, dict) else 0
        )
        retry_count = (
            int(bulk_result.get("retry_count", 0) or 0) if isinstance(bulk_result, dict) else 0
        )
        skip_count = (
            int(bulk_result.get("skip_count", 0) or 0) if isinstance(bulk_result, dict) else 0
        )
        failed_count = (
            int(bulk_result.get("failed_count", 0) or 0) if isinstance(bulk_result, dict) else 0
        )
        if (new_count + retry_count) > 0:
            request.app.state.runtime.manual_article_wake_event.set()

    llm_worker_waiting = not await settings_repo.is_worker_enabled(
        request.app.state.runtime.db,
        "llm",
    )

    toast_message = _build_article_request_summary_message(
        txt,
        new_count=new_count,
        retry_count=retry_count,
        skip_count=skip_count,
        failed_count=failed_count,
        llm_worker_waiting=llm_worker_waiting,
    )
    toast_tone = _resolve_article_request_toast_tone(
        new_count=new_count,
        retry_count=retry_count,
        skip_count=skip_count,
        failed_count=failed_count,
    )

    response = await video_detail(video_id=video_id, request=request)
    response.headers.update(_video_article_request_toast_header(toast_message, toast_tone))
    return response


@router.get("/video-detail/{video_id}")
async def video_detail(video_id: str, request: Request):
    if request.method == "GET":
        redirect = full_page_redirect_for_non_fragment_request(request, f"/videos/{video_id}")
        if redirect is not None:
            return redirect

    context = await build_video_detail_context(
        request,
        video_id=video_id,
        transcript_retry_done=False,
        mark_viewed=True,
    )
    status_code = 200 if context.get("video") else 404
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_detail.html",
        context=context,
        status_code=status_code,
    )


@router.get("/search-results")
async def search_results(request: Request, q: str = Query(default="")):
    redirect = full_page_redirect_for_non_fragment_request(
        request,
        _page_url_with_request_query("/", request),
    )
    if redirect is not None:
        return redirect

    results = await videos_repo.search_documents(request.app.state.runtime.db, q) if q else []
    context = await build_template_context(request, results=results, q=q)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/search_results.html",
        context=context,
    )


@router.get("/status-badge/{video_id}")
async def status_badge(video_id: str, request: Request):
    redirect = full_page_redirect_for_non_fragment_request(request, f"/videos/{video_id}")
    if redirect is not None:
        return redirect

    video = await videos_repo.get_video(request.app.state.runtime.db, video_id)
    status = video["pipeline_status"] if video else "unknown"
    context = await build_template_context(request, status=status)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/status_badge.html",
        context=context,
    )
