from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs
from starlette.datastructures import UploadFile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app import repository
from app.download_error_registry import build_download_error_payload
from app.domains.downloads import enqueue_video_download, retry_download_job as retry_download_job_request
from app.i18n import SUPPORTED_LANGUAGES, get_texts, normalize_language
from app.services.downloads import is_ffmpeg_available, validate_download_output_dir
from app.services.llm_runtime import (
    LlmRuntimeStatus,
    is_runtime_ready_for_resume,
    resolve_llm_runtime_status,
    runtime_reason_text_key,
)
from app.timezone_policy import SUPPORTED_TIMEZONES, normalize_timezone
from app.services.bulk_channels import (
    collect_inputs_from_sources,
    parse_takeout_entries,
    resolve_bulk_inputs,
)
from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS,
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    TRANSCRIPT_REQUEST_HEADER_PROFILE,
    compact_header_overrides,
    default_transcript_request_headers,
    format_headers_multiline,
    merge_with_default_headers,
    parse_headers_from_fields,
    parse_headers_multiline,
    validate_complete_header_fields,
)

router = APIRouter(prefix="/api", tags=["api"])
logger = logging.getLogger(__name__)
ARTICLE_REQUEST_BULK_LIMIT = 10


def _parse_bool_input(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _build_transcript_header_payload(overrides: dict[str, str]) -> dict[str, object]:
    compact = compact_header_overrides(overrides, strict=False)
    values = merge_with_default_headers(compact)
    defaults = default_transcript_request_headers()
    return {
        "profile": TRANSCRIPT_REQUEST_HEADER_PROFILE,
        "keys": list(TRANSCRIPT_REQUEST_HEADER_KEYS),
        "field_names": dict(TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS),
        "defaults": defaults,
        "values": values,
        "multiline": format_headers_multiline(values),
    }


def _build_llm_runtime_toast_header(message: str, tone: str) -> dict[str, str]:
    payload = {
        "llm-runtime-toast": {
            "message": message,
            "tone": tone,
        }
    }
    return {"HX-Trigger": json.dumps(payload, ensure_ascii=True)}


async def _resolve_llm_runtime_status(request: Request) -> dict[str, object]:
    llm_settings = await repository.get_llm_settings(request.app.state.runtime.db)
    runtime_issue = await repository.get_llm_runtime_issue(request.app.state.runtime.db)
    pending_count = await repository.count_llm_pending_videos(request.app.state.runtime.db)
    status = resolve_llm_runtime_status(
        llm_client=request.app.state.runtime.llm_client,
        llm_settings=llm_settings,
        runtime_issue=runtime_issue,
        pending_count=pending_count,
    )
    return {
        "ready": status.ready,
        "code": status.code,
        "reason": status.reason,
        "reason_text_key": runtime_reason_text_key(status.code),
        "providers_to_try": status.providers_to_try,
        "warnings": status.warnings,
        "pending_count": status.pending_count,
    }


@router.get("/categories")
async def get_categories(request: Request):
    return await repository.list_categories(request.app.state.runtime.db)


@router.post("/categories")
async def create_category(request: Request):
    content_type = request.headers.get("content-type", "")
    name = ""
    if "application/json" in content_type:
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        name = str((parsed.get("name") or [""])[0]).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        category = await repository.create_category(request.app.state.runtime.db, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return category


@router.put("/categories/reorder")
async def reorder_categories(request: Request):
    content_type = request.headers.get("content-type", "")
    ordered_ids: list[int] = []
    if "application/json" in content_type:
        payload = await request.json()
        raw_ids = payload.get("ordered_ids", [])
        ordered_ids = [int(i) for i in raw_ids]
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        ordered_ids = [int(i) for i in parsed.get("ordered_ids", [])]
    if not ordered_ids:
        raise HTTPException(status_code=400, detail="ordered_ids is required")
    updated = await repository.reorder_categories(request.app.state.runtime.db, ordered_ids)
    return {"ok": True, "updated": updated}


@router.put("/categories/{category_id}")
async def update_category(category_id: int, request: Request):
    content_type = request.headers.get("content-type", "")
    name: str | None = None
    llm_enabled: bool | None = None
    if "application/json" in content_type:
        payload = await request.json()
        if "name" in payload:
            name = str(payload.get("name", "")).strip()
        if "llm_enabled" in payload:
            llm_enabled = _parse_bool_input(payload.get("llm_enabled"), default=True)
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        if "name" in parsed:
            name = str(parsed["name"][0]).strip()
        if "llm_enabled" in parsed:
            llm_enabled = _parse_bool_input(parsed["llm_enabled"][0], default=True)

    result: dict[str, object] = {"ok": True}
    try:
        if name is not None:
            rows = await repository.rename_category(request.app.state.runtime.db, category_id, name)
            if rows == 0:
                raise HTTPException(status_code=404, detail="category not found")
            result["renamed"] = True
        if llm_enabled is not None:
            rows = await repository.update_category_llm_enabled(request.app.state.runtime.db, category_id, llm_enabled)
            if rows == 0:
                raise HTTPException(status_code=404, detail="category not found")
            result["llm_enabled"] = llm_enabled
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, request: Request):
    try:
        result = await repository.delete_category(request.app.state.runtime.db, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.post("/categories/{category_id}/channels")
async def move_channels_to_category(category_id: int, request: Request):
    content_type = request.headers.get("content-type", "")
    channel_ids: list[str] = []
    if "application/json" in content_type:
        payload = await request.json()
        channel_ids = [str(c).strip() for c in payload.get("channel_ids", []) if str(c).strip()]
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        channel_ids = [str(c).strip() for c in parsed.get("channel_ids", []) if str(c).strip()]
    if not channel_ids:
        raise HTTPException(status_code=400, detail="channel_ids is required")
    try:
        moved = await repository.move_channels_to_category(
            request.app.state.runtime.db, channel_ids, category_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "moved": moved}


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
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        channel_id = str((parsed.get("channel_id") or [""])[0]).strip()
        channel_name = str((parsed.get("channel_name") or [""])[0]).strip()

    if not channel_id or not channel_name:
        raise HTTPException(status_code=400, detail="channel_id and channel_name are required")

    return await repository.add_channel(
        request.app.state.runtime.db,
        channel_id=channel_id,
        channel_name=channel_name,
    )


@router.post("/channels/bulk/resolve")
async def resolve_bulk_channels(request: Request):
    bulk_text = ""
    takeout_data = parse_takeout_entries("takeout.txt", b"")
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await request.json()
        bulk_text = str(payload.get("bulk_text", ""))
        raw_entries = [str(item).strip() for item in payload.get("takeout_entries", []) if str(item).strip()]
        if raw_entries:
            takeout_data = parse_takeout_entries("takeout.txt", "\n".join(raw_entries).encode("utf-8"))
        else:
            takeout_data = parse_takeout_entries("takeout.txt", b"")
    else:
        form = await request.form()
        bulk_text = str(form.get("bulk_text", ""))
        upload = form.get("takeout_file")
        if isinstance(upload, UploadFile):
            file_content = await upload.read()
            takeout_data = parse_takeout_entries(
                filename=upload.filename or "takeout.txt",
                content=file_content,
            )

    collected = collect_inputs_from_sources(
        bulk_text=bulk_text,
        takeout_data=takeout_data,
    )
    if not collected["inputs"] and not collected["direct_channels"]:
        return {
            "ok": True,
            "total_inputs": 0,
            "resolved": [],
            "needs_selection": [],
            "failed": [],
        }

    return await resolve_bulk_inputs(
        inputs=collected["inputs"],
        direct_channels=collected["direct_channels"],
        resolver=request.app.state.runtime.channel_resolver,
    )


def _normalize_commit_items(raw_items: list[dict]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    normalized: list[tuple[str, str]] = []
    for item in raw_items:
        channel_id = str(item.get("channel_id", "")).strip()
        channel_name = str(item.get("channel_name", "")).strip()
        if not channel_id or not channel_name:
            continue
        if channel_id in seen:
            continue
        seen.add(channel_id)
        normalized.append((channel_id, channel_name))
    return normalized


@router.post("/channels/bulk/commit")
async def commit_bulk_channels(request: Request):
    items: list[dict] = []
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        items = payload.get("items", [])
    else:
        form = await request.form()
        resolved_ids = list(form.getlist("resolved_channel_id"))
        resolved_names = list(form.getlist("resolved_channel_name"))
        for channel_id, channel_name in zip(resolved_ids, resolved_names):
            items.append({"channel_id": channel_id, "channel_name": channel_name})

        for key in form.keys():
            if not key.startswith("candidate_select_"):
                continue
            packed = str(form.get(key, ""))
            if "|||" not in packed:
                continue
            channel_id, channel_name = packed.split("|||", 1)
            items.append({"channel_id": channel_id, "channel_name": channel_name})

    normalized = _normalize_commit_items(items)
    if not normalized:
        raise HTTPException(status_code=400, detail="no valid channels to save")

    saved = 0
    for channel_id, channel_name in normalized:
        await repository.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        saved += 1
    return {"ok": True, "saved": saved}


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str, request: Request):
    result = await repository.delete_channels_with_related_data(
        request.app.state.runtime.db,
        [channel_id],
    )
    request.app.state.runtime.rss_cache.pop(channel_id, None)
    if result["deleted_channels"] == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {
        "ok": True,
        "channel_id": channel_id,
        "deleted_channels": result["deleted_channels"],
        "deleted_videos": result["deleted_videos"],
    }


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
        limit = await repository.get_videos_per_page_setting(request.app.state.runtime.db)

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
    if not await repository.is_worker_enabled(request.app.state.runtime.db, "rss"):
        return {"ok": True, "triggered": False, "reason": "rss_worker_disabled"}
    request.app.state.runtime.poll_now_event.set()
    return {"ok": True, "triggered": True}


@router.get("/queue/poll")
async def queue_poll(request: Request):
    db = request.app.state.runtime.db
    transcript_items = await repository.list_queue_items(
        db, repository.TRANSCRIPT_QUEUE_STATUSES,
    )
    llm_items = await repository.list_queue_items(
        db, repository.LLM_QUEUE_STATUSES,
    )
    counts = await repository.queue_status(db)
    workers = await repository.get_worker_settings(db)
    guard = await repository.get_transcript_guard_state(db)
    badge_count = (
        counts.get("transcript_pending", 0)
        + counts.get("transcript_processing", 0)
        + counts.get("llm_pending", 0)
        + counts.get("llm_processing", 0)
    )
    return {
        "transcript_items": transcript_items,
        "llm_items": llm_items,
        "counts": counts,
        "badge_count": badge_count,
        "workers": {
            "transcript": workers.get("transcript", True),
            "llm": workers.get("llm", True),
        },
        "transcript_guard": {
            "breaker_state": guard.get("breaker_state", "closed"),
            "cooldown_until": guard.get("cooldown_until"),
            "adaptive_factor": guard.get("adaptive_factor", 1.0),
        },
    }


@router.get("/status")
async def status(request: Request):
    return await repository.queue_status(request.app.state.runtime.db)


@router.post("/videos/{video_id}/retry")
async def retry_video(video_id: str, request: Request):
    affected = await repository.mark_video_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Retry target not found")
    return {"ok": True, "video_id": video_id}


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

    bulk_result = await repository.enqueue_manual_article_jobs(
        request.app.state.runtime.db,
        video_ids=video_ids,
    )
    new_count = int(bulk_result.get("new_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    retry_count = int(bulk_result.get("retry_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    skip_count = int(bulk_result.get("skip_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    failed_count = int(bulk_result.get("failed_count", 0) or 0) if isinstance(bulk_result, dict) else 0

    if (new_count + retry_count) > 0:
        request.app.state.runtime.manual_article_wake_event.set()

    llm_worker_waiting = not await repository.is_worker_enabled(
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
    affected = await repository.reset_transcript_for_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Transcript retry target not found")
    return {"ok": True, "video_id": video_id}


@router.post("/videos/{video_id}/downloads")
async def request_video_download(video_id: str, request: Request):
    video = await repository.get_video(request.app.state.runtime.db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    defaults = await repository.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    content_type = request.headers.get("content-type", "")
    quality = str(defaults["quality"])
    overwrite = bool(defaults["overwrite"])
    if "application/json" in content_type:
        payload = await request.json()
        if "quality" in payload:
            quality = repository.normalize_download_quality(str(payload.get("quality")))
        if "overwrite" in payload:
            overwrite = _parse_bool_input(payload.get("overwrite"), default=overwrite)
    else:
        form = await request.form()
        if "quality" in form:
            quality = repository.normalize_download_quality(str(form.get("quality")))
        if "overwrite" in form:
            overwrite = _parse_bool_input(form.get("overwrite"), default=overwrite)

    logger.info(
        "event=downloads.enqueue_requested video_id=%s quality=%s overwrite=%s",
        str(video["video_id"]),
        quality,
        overwrite,
        extra={"event": "downloads.enqueue_requested"},
    )
    if not is_ffmpeg_available():
        return JSONResponse(
            status_code=409,
            content=build_download_error_payload(
                code="ffmpeg_missing",
                message="ffmpeg is not installed",
                queued=False,
                retried=False,
            ),
        )

    operation = await enqueue_video_download(
        request.app.state.runtime.db,
        video=video,
        quality=quality,
        overwrite=overwrite,
        default_output_dir=str(defaults.get("output_dir") or request.app.state.runtime.config.download_dir),
        skip_environment_check=True,
    )

    if operation.payload.get("queued") is True:
        request.app.state.runtime.download_wake_event.set()
    if operation.ok and operation.payload.get("duplicate") is True:
        logger.info(
            "event=downloads.enqueue_duplicate video_id=%s job_id=%s status=%s",
            str(video["video_id"]),
            operation.payload.get("job_id"),
            operation.payload.get("status"),
            extra={"event": "downloads.enqueue_duplicate"},
        )
    elif operation.ok and operation.payload.get("queued") is True:
        logger.info(
            "event=downloads.enqueue_created video_id=%s job_id=%s quality=%s overwrite=%s",
            str(video["video_id"]),
            operation.payload.get("job_id"),
            operation.payload.get("quality"),
            bool(operation.payload.get("overwrite")),
            extra={"event": "downloads.enqueue_created"},
        )
    elif not operation.ok:
        logger.warning(
            "event=downloads.enqueue_rejected video_id=%s code=%s",
            str(video["video_id"]),
            str(operation.payload.get("code") or "unknown"),
            extra={"event": "downloads.enqueue_rejected", "code": str(operation.payload.get("code") or "unknown")},
        )
    return JSONResponse(status_code=operation.status_code, content=operation.payload)


@router.get("/downloads")
async def get_downloads(
    request: Request,
    status: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    normalized_status = repository.normalize_download_status_filter(status)
    jobs = await repository.list_download_jobs(
        request.app.state.runtime.db,
        status=normalized_status,
        page=page,
        limit=limit,
    )
    total = await repository.count_download_jobs(
        request.app.state.runtime.db,
        status=normalized_status,
    )
    counts = await repository.count_download_jobs_by_status(request.app.state.runtime.db)
    return {
        "ok": True,
        "status": normalized_status,
        "page": page,
        "limit": limit,
        "total": total,
        "counts": counts,
        "jobs": jobs,
    }


@router.get("/downloads/progress")
async def get_download_progress(
    request: Request,
    after_event_id: int = Query(default=0, ge=0),
    event_limit: int = Query(default=100, ge=1, le=200),
):
    payload = await repository.get_download_progress(
        request.app.state.runtime.db,
        after_event_id=after_event_id,
        event_limit=event_limit,
    )
    counts = payload["counts"]
    event_count = len(payload["events"])
    if event_count > 0:
        logger.debug(
            "event=downloads.progress_events after_event_id=%s returned_events=%s latest_event_id=%s active=%s",
            after_event_id,
            event_count,
            int(payload["latest_event_id"]),
            int(counts[repository.DOWNLOAD_STATUS_PENDING]) + int(counts[repository.DOWNLOAD_STATUS_RUNNING]),
            extra={"event": "downloads.progress_events"},
        )
    return {
        "ok": True,
        "pending_count": int(counts[repository.DOWNLOAD_STATUS_PENDING]),
        "running_count": int(counts[repository.DOWNLOAD_STATUS_RUNNING]),
        "succeeded_count": int(counts[repository.DOWNLOAD_STATUS_SUCCEEDED]),
        "failed_count": int(counts[repository.DOWNLOAD_STATUS_FAILED]),
        "active_count": int(counts[repository.DOWNLOAD_STATUS_PENDING])
        + int(counts[repository.DOWNLOAD_STATUS_RUNNING]),
        "latest_event_id": int(payload["latest_event_id"]),
        "events": payload["events"],
    }


@router.post("/downloads/{job_id}/retry")
async def retry_download(job_id: int, request: Request):
    logger.info(
        "event=downloads.retry_requested job_id=%s",
        job_id,
        extra={"event": "downloads.retry_requested"},
    )
    if not is_ffmpeg_available():
        return JSONResponse(
            status_code=409,
            content=build_download_error_payload(
                code="ffmpeg_missing",
                message="ffmpeg is not installed",
                queued=False,
                retried=False,
            ),
        )
    operation = await retry_download_job_request(request.app.state.runtime.db, job_id=job_id)
    if operation.ok:
        request.app.state.runtime.download_wake_event.set()
        job = operation.payload.get("job")
        logger.info(
            "event=downloads.retry_queued job_id=%s status=%s attempt_count=%s",
            job_id,
            job.get("status") if isinstance(job, dict) else "",
            job.get("attempt_count") if isinstance(job, dict) else "",
            extra={"event": "downloads.retry_queued"},
        )
    else:
        logger.warning(
            "event=downloads.retry_rejected job_id=%s reason=%s",
            job_id,
            str(operation.payload.get("code") or "unknown"),
            extra={"event": "downloads.retry_rejected", "code": str(operation.payload.get("code") or "unknown")},
        )
    return JSONResponse(status_code=operation.status_code, content=operation.payload)


@router.get("/settings")
async def get_settings(request: Request):
    language = await repository.get_setting(
        request.app.state.runtime.db,
        key="language",
        default="ko",
    )
    workers = await repository.get_worker_settings(request.app.state.runtime.db)
    policy = await repository.get_policy_settings(request.app.state.runtime.db)
    videos_per_page = await repository.get_videos_per_page_setting(request.app.state.runtime.db)
    transcript_guard = await repository.get_transcript_guard_state(request.app.state.runtime.db)
    timezone_value = await repository.get_setting(
        request.app.state.runtime.db,
        key="timezone",
        default="Asia/Seoul",
    )
    transcript_request_header_overrides = await repository.get_transcript_request_header_overrides(
        request.app.state.runtime.db
    )
    transcript_request_headers = _build_transcript_header_payload(transcript_request_header_overrides)
    download_defaults = await repository.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    llm_settings = await repository.get_llm_settings(request.app.state.runtime.db)
    llm_runtime_status = await _resolve_llm_runtime_status(request)
    return {
        "language": normalize_language(language),
        "timezone": normalize_timezone(timezone_value),
        "workers": workers,
        "policy": policy,
        "videos_per_page": videos_per_page,
        "transcript_guard": transcript_guard,
        "transcript_request_headers": transcript_request_headers,
        "download_defaults": download_defaults,
        "llm_settings": llm_settings,
        "llm_runtime_status": llm_runtime_status,
        "ffmpeg_available": is_ffmpeg_available(),
    }


@router.post("/settings/transcript-guard/reset")
async def reset_transcript_guard(request: Request):
    guard = await repository.reset_transcript_guard_state(request.app.state.runtime.db)
    return {
        "ok": True,
        "transcript_guard": guard,
    }


@router.put("/settings/transcript-request-headers")
async def set_transcript_request_headers(request: Request):
    content_type = request.headers.get("content-type", "")
    try:
        parsed: dict[str, str] = {}
        has_field_input = False
        raw_text = ""
        if "application/json" in content_type:
            payload = await request.json() or {}
            if isinstance(payload, dict):
                parsed, has_field_input = parse_headers_from_fields(payload)
                raw_text = str(
                    payload.get("headers_text", payload.get("transcript_request_headers", "")) or ""
                )
            if has_field_input and raw_text.strip():
                raise ValueError("mixed header input modes are not allowed")
            if has_field_input:
                validate_complete_header_fields(payload if isinstance(payload, dict) else {})
            elif raw_text.strip():
                parsed = parse_headers_multiline(raw_text)
            else:
                raise ValueError("empty header payload is not allowed")
        else:
            form = await request.form()
            form_payload = {key: form.get(key) for key in form.keys()}
            parsed, has_field_input = parse_headers_from_fields(form_payload)
            raw_text = str(form.get("transcript_request_headers", "") or "")
            if has_field_input and raw_text.strip():
                raise ValueError("mixed header input modes are not allowed")
            if has_field_input:
                validate_complete_header_fields(form_payload)
            elif raw_text.strip():
                parsed = parse_headers_multiline(raw_text)
            else:
                raise ValueError("empty header payload is not allowed")

        overrides = compact_header_overrides(parsed, strict=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    saved_overrides = await repository.save_transcript_request_header_overrides(
        request.app.state.runtime.db,
        overrides,
    )
    applied_values = merge_with_default_headers(saved_overrides)
    request.app.state.runtime.transcript_service.apply_transcript_request_headers(applied_values)
    payload = _build_transcript_header_payload(saved_overrides)
    return {
        "ok": True,
        "transcript_request_headers": payload,
    }


@router.put("/settings/llm")
async def set_llm_settings(request: Request):
    content_type = request.headers.get("content-type", "")
    provider_primary: str | None = None
    provider_fallback: str | None = None
    prompt_template: str | None = None

    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="llm payload must be object")
        if "provider_primary" in payload:
            provider_primary = str(payload.get("provider_primary", "")).strip().lower()
        if "provider_fallback" in payload:
            provider_fallback = str(payload.get("provider_fallback", "")).strip().lower()
        if "prompt_template" in payload:
            prompt_template = str(payload.get("prompt_template", ""))
    else:
        form = await request.form()
        if "llm_provider_primary" in form:
            provider_primary = str(form.get("llm_provider_primary", "")).strip().lower()
        if "llm_provider_fallback" in form:
            provider_fallback = str(form.get("llm_provider_fallback", "")).strip().lower()
        if "llm_prompt_template" in form:
            prompt_template = str(form.get("llm_prompt_template", ""))

    if provider_primary is None and provider_fallback is None and prompt_template is None:
        raise HTTPException(status_code=400, detail="empty llm settings payload")

    try:
        saved = await repository.set_llm_settings(
            request.app.state.runtime.db,
            provider_primary=provider_primary,
            provider_fallback=provider_fallback,
            prompt_template=prompt_template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "ok": True,
        "llm_settings": saved,
    }


@router.get("/settings/llm/runtime-status")
async def get_llm_runtime_status(request: Request):
    return await _resolve_llm_runtime_status(request)


@router.post("/settings/llm/resume")
async def resume_llm_runtime(request: Request):
    language = normalize_language(
        await repository.get_setting(
            request.app.state.runtime.db,
            key="language",
            default="ko",
        )
    )
    txt = get_texts(language)
    status_payload = await _resolve_llm_runtime_status(request)
    status = LlmRuntimeStatus(
        ready=bool(status_payload.get("ready")),
        code=str(status_payload.get("code") or ""),
        reason=str(status_payload.get("reason") or ""),
        providers_to_try=list(status_payload.get("providers_to_try") or []),
        warnings=list(status_payload.get("warnings") or []),
        pending_count=int(status_payload.get("pending_count") or 0),
    )
    if not is_runtime_ready_for_resume(status):
        reason_key = runtime_reason_text_key(str(status_payload.get("code") or ""))
        reason_text = txt.get(reason_key, txt["settings_llm_runtime_reason_generic"])
        message = txt["settings_llm_runtime_resume_blocked_toast"].format(reason=reason_text)
        return JSONResponse(
            status_code=409,
            content={"ok": False, "status": status_payload},
            headers=_build_llm_runtime_toast_header(message, "error"),
        )

    pending_count = int(status_payload["pending_count"])
    if pending_count > 0:
        request.app.state.runtime.llm_wake_event.set()
        message = txt["settings_llm_runtime_resume_requested_toast"].format(count=pending_count)
        tone = "success"
    else:
        message = txt["settings_llm_runtime_resume_no_pending_toast"]
        tone = "info"
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "resumed_count": pending_count,
            "status": status_payload,
        },
        headers=_build_llm_runtime_toast_header(message, tone),
    )


@router.put("/settings/language")
async def set_language(request: Request):
    content_type = request.headers.get("content-type", "")
    value = ""
    if "application/json" in content_type:
        payload = await request.json()
        value = str(payload.get("language", "")).strip().lower()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        value = str((parsed.get("language") or [""])[0]).strip().lower()

    if value not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="language must be one of: ko, en")

    await repository.set_setting(request.app.state.runtime.db, key="language", value=value)
    return {"ok": True, "language": value}


@router.put("/settings/timezone")
async def set_timezone(request: Request):
    content_type = request.headers.get("content-type", "")
    value = ""
    if "application/json" in content_type:
        payload = await request.json()
        value = str(payload.get("timezone", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        value = str((parsed.get("timezone") or [""])[0]).strip()

    if value not in SUPPORTED_TIMEZONES:
        raise HTTPException(status_code=400, detail="unsupported timezone")

    await repository.set_setting(request.app.state.runtime.db, key="timezone", value=value)
    return {"ok": True, "timezone": value}


@router.put("/settings/videos-per-page")
async def set_videos_per_page(request: Request):
    content_type = request.headers.get("content-type", "")
    raw_value = ""
    if "application/json" in content_type:
        payload = await request.json()
        raw_value = str(payload.get("videos_per_page", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        raw_value = str((parsed.get("videos_per_page") or [""])[0]).strip()

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="videos_per_page must be integer")

    saved = await repository.set_videos_per_page_setting(request.app.state.runtime.db, value)
    return {"ok": True, "videos_per_page": saved}


@router.put("/settings/workers")
async def set_workers(request: Request):
    defaults = repository.WORKER_SETTING_DEFAULTS
    values = await repository.get_worker_settings(request.app.state.runtime.db)
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await request.json()
        workers_payload = payload.get("workers", {})
        for worker in defaults:
            if worker not in workers_payload:
                continue
            values[worker] = _parse_bool_input(
                workers_payload.get(worker),
                default=values.get(worker, defaults[worker]),
            )
    else:
        form = await request.form()
        for worker in defaults:
            # HTML checkbox: checked => "on", unchecked => missing.
            values[worker] = _parse_bool_input(
                form.get(worker),
                default=False,
            )

    saved = await repository.set_worker_settings(request.app.state.runtime.db, values)
    return {"ok": True, "workers": saved}


@router.put("/settings/policy")
async def set_policy(request: Request):
    content_type = request.headers.get("content-type", "")
    lookback_value: int | None = None
    retention_value: int | None = None
    feed_mode_value: str | None = None

    try:
        if "application/json" in content_type:
            payload = await request.json()
            if "rss_bootstrap_lookback_days" in payload:
                lookback_value = int(payload.get("rss_bootstrap_lookback_days"))
            if "retention_days" in payload:
                retention_value = int(payload.get("retention_days"))
            if "rss_feed_mode" in payload:
                feed_mode_value = str(payload["rss_feed_mode"])
        else:
            form = await request.form()
            lookback_raw = str(form.get("rss_bootstrap_lookback_days", "")).strip()
            retention_raw = str(form.get("retention_days", "")).strip()
            if lookback_raw:
                lookback_value = int(lookback_raw)
            if retention_raw:
                retention_value = int(retention_raw)
            feed_mode_raw = str(form.get("rss_feed_mode", "")).strip()
            if feed_mode_raw:
                feed_mode_value = feed_mode_raw
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="policy values must be integers")

    saved = await repository.set_policy_settings(
        request.app.state.runtime.db,
        rss_bootstrap_lookback_days=lookback_value,
        retention_days=retention_value,
        rss_feed_mode=feed_mode_value,
    )
    return {"ok": True, "policy": saved}


@router.put("/settings/downloads")
async def set_download_defaults(request: Request):
    content_type = request.headers.get("content-type", "")
    quality: str | None = None
    overwrite: bool | None = None
    output_dir: str | None = None
    allowed_qualities = ", ".join(["2160", "1440", "1080", "720", "480"])
    if "application/json" in content_type:
        payload = await request.json()
        if "quality" in payload:
            parsed_quality = str(payload.get("quality", "")).strip().lower()
            if parsed_quality not in repository.DOWNLOAD_QUALITY_OPTIONS:
                raise HTTPException(status_code=400, detail=f"quality must be one of: {allowed_qualities}")
            quality = parsed_quality
        if "overwrite" in payload:
            overwrite = _parse_bool_input(payload.get("overwrite"), default=False)
        if "output_dir" in payload or "download_output_dir" in payload:
            output_dir = str(payload.get("output_dir", payload.get("download_output_dir", ""))).strip()
    else:
        form = await request.form()
        if "download_quality" in form:
            parsed_quality = str(form.get("download_quality", "")).strip().lower()
            if parsed_quality not in repository.DOWNLOAD_QUALITY_OPTIONS:
                raise HTTPException(status_code=400, detail=f"quality must be one of: {allowed_qualities}")
            quality = parsed_quality
        overwrite = _parse_bool_input(form.get("download_overwrite"), default=False)
        if "download_output_dir" in form:
            output_dir = str(form.get("download_output_dir", "")).strip()

    if quality is None and overwrite is None and output_dir is None:
        raise HTTPException(status_code=400, detail="empty download settings payload")

    if output_dir is not None:
        validation = validate_download_output_dir(
            output_dir,
            require_absolute=True,
            require_existing=True,
        )
        if not validation.ok:
            raise HTTPException(status_code=400, detail=validation.error_code or "download_path_invalid")
        output_dir = validation.normalized_path

    try:
        saved = await repository.set_download_default_settings(
            request.app.state.runtime.db,
            quality=quality,
            overwrite=overwrite,
            output_dir=output_dir,
            default_output_dir=request.app.state.runtime.config.download_dir,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    logger.info(
        "event=downloads.settings_saved quality=%s overwrite=%s",
        saved.get("quality"),
        saved.get("overwrite"),
        extra={"event": "downloads.settings_saved"},
    )
    return {
        "ok": True,
        "download_defaults": saved,
    }
