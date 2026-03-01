from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.download_error_registry import build_download_error_payload
from app.domains.downloads import enqueue_video_download, retry_download_job as retry_download_job_request
from app.repositories import downloads as downloads_repo
from app.repositories import videos as videos_repo
from app.services.downloads import is_ffmpeg_available, validate_download_output_dir

router = APIRouter(tags=["api"])
logger = logging.getLogger("app.routers.api")


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


@router.post("/videos/{video_id}/downloads")
async def request_video_download(video_id: str, request: Request):
    video = await videos_repo.get_video(request.app.state.runtime.db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    defaults = await downloads_repo.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    content_type = request.headers.get("content-type", "")
    quality = str(defaults["quality"])
    overwrite = bool(defaults["overwrite"])
    if "application/json" in content_type:
        payload = await request.json()
        if "quality" in payload:
            quality = downloads_repo.normalize_download_quality(str(payload.get("quality")))
        if "overwrite" in payload:
            overwrite = _parse_bool_input(payload.get("overwrite"), default=overwrite)
    else:
        form = await request.form()
        if "quality" in form:
            quality = downloads_repo.normalize_download_quality(str(form.get("quality")))
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
    normalized_status = downloads_repo.normalize_download_status_filter(status)
    jobs = await downloads_repo.list_download_jobs(
        request.app.state.runtime.db,
        status=normalized_status,
        page=page,
        limit=limit,
    )
    total = await downloads_repo.count_download_jobs(
        request.app.state.runtime.db,
        status=normalized_status,
    )
    counts = await downloads_repo.count_download_jobs_by_status(request.app.state.runtime.db)
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
    payload = await downloads_repo.get_download_progress(
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
            int(counts[downloads_repo.DOWNLOAD_STATUS_PENDING]) + int(counts[downloads_repo.DOWNLOAD_STATUS_RUNNING]),
            extra={"event": "downloads.progress_events"},
        )
    return {
        "ok": True,
        "pending_count": int(counts[downloads_repo.DOWNLOAD_STATUS_PENDING]),
        "running_count": int(counts[downloads_repo.DOWNLOAD_STATUS_RUNNING]),
        "succeeded_count": int(counts[downloads_repo.DOWNLOAD_STATUS_SUCCEEDED]),
        "failed_count": int(counts[downloads_repo.DOWNLOAD_STATUS_FAILED]),
        "active_count": int(counts[downloads_repo.DOWNLOAD_STATUS_PENDING])
        + int(counts[downloads_repo.DOWNLOAD_STATUS_RUNNING]),
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
            if parsed_quality not in downloads_repo.DOWNLOAD_QUALITY_OPTIONS:
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
            if parsed_quality not in downloads_repo.DOWNLOAD_QUALITY_OPTIONS:
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
        saved = await downloads_repo.set_download_default_settings(
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
