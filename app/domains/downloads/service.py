from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from app.download_error_registry import build_download_error_payload, get_download_error_spec
from app.domains.downloads.types import BulkEnqueueResult, DownloadActionResult, DownloadFileTargetResult
from app.repositories import downloads as downloads_repo
from app.repositories import videos as videos_repo
from app.services.downloads import is_ffmpeg_available, validate_download_output_dir


def resolve_worker_timeout_seconds(*, quality: str, base_timeout_seconds: int) -> int:
    base_timeout = max(1, int(base_timeout_seconds))
    return max(base_timeout, 3600) if str(quality).strip() == "2160" else base_timeout


async def recover_stuck_running_jobs(db: aiosqlite.Connection) -> int:
    return await downloads_repo.recover_stuck_download_jobs(db)


def _download_environment_error(*, output_dir: str) -> DownloadActionResult | None:
    if not is_ffmpeg_available():
        spec = get_download_error_spec("ffmpeg_missing")
        return DownloadActionResult(
            ok=False,
            status_code=spec.status_code,
            payload=build_download_error_payload(
                code=spec.code,
                message="ffmpeg is not installed",
                queued=False,
                retried=False,
            ),
        )

    validation = validate_download_output_dir(
        output_dir,
        require_absolute=True,
        require_existing=True,
    )
    if validation.ok:
        return None

    code = validation.error_code or "download_path_invalid"
    spec = get_download_error_spec(code)
    return DownloadActionResult(
        ok=False,
        status_code=spec.status_code,
        payload=build_download_error_payload(
            code=code,
            message=validation.error_message or "download output directory is unavailable",
            queued=False,
            retried=False,
        ),
    )


async def enqueue_video_download(
    db: aiosqlite.Connection,
    *,
    video: dict[str, Any],
    quality: str,
    overwrite: bool,
    default_output_dir: str,
    skip_environment_check: bool = False,
) -> DownloadActionResult:
    if not skip_environment_check:
        env_error = _download_environment_error(output_dir=default_output_dir)
        if env_error is not None:
            return env_error

    validation = validate_download_output_dir(
        default_output_dir,
        require_absolute=True,
        require_existing=True,
    )
    target_dir = validation.normalized_path
    result = await downloads_repo.create_download_job(
        db,
        video_id=str(video["video_id"]),
        video_title=str(video.get("title") or video["video_id"]),
        quality=quality,
        overwrite=overwrite,
        target_dir=target_dir,
    )
    job = result.get("job") or {}
    if bool(result.get("created")):
        return DownloadActionResult(
            ok=True,
            status_code=202,
            payload={
                "ok": True,
                "queued": True,
                "duplicate": False,
                "job_id": job.get("id"),
                "status": job.get("status"),
                "video_id": job.get("video_id"),
                "quality": job.get("quality"),
                "overwrite": bool(job.get("overwrite")),
                "message_key": "download_toast_queued",
                "tone": "success",
                "retryable": False,
            },
        )

    return DownloadActionResult(
        ok=True,
        status_code=200,
        payload={
            "ok": True,
            "queued": False,
            "duplicate": True,
            "job_id": job.get("id"),
            "status": job.get("status"),
            "video_id": job.get("video_id"),
            "quality": job.get("quality"),
            "overwrite": bool(job.get("overwrite")),
            "message_key": "download_toast_duplicate",
            "tone": "info",
            "retryable": True,
        },
    )


async def retry_download_job(db: aiosqlite.Connection, *, job_id: int) -> DownloadActionResult:
    result = await downloads_repo.retry_download_job(db, job_id)
    if int(result.get("updated", 0)) <= 0:
        reason = str(result.get("reason", "unknown"))
        spec = get_download_error_spec(reason)
        return DownloadActionResult(
            ok=False,
            status_code=spec.status_code,
            payload=build_download_error_payload(
                code=reason,
                message=f"Download job retry failed: {reason}",
                retried=False,
                queued=False,
            ),
        )

    job = await downloads_repo.get_download_job(db, job_id)
    return DownloadActionResult(
        ok=True,
        status_code=200,
        payload={
            "ok": True,
            "retried": True,
            "job": job,
            "message_key": "download_toast_retry_queued",
            "tone": "success",
            "retryable": False,
        },
    )


async def enqueue_bulk_downloads(
    db: aiosqlite.Connection,
    *,
    video_ids: list[str],
    default_output_dir: str,
    quality: str,
    overwrite: bool,
) -> BulkEnqueueResult:
    result = BulkEnqueueResult()

    env_error = _download_environment_error(output_dir=default_output_dir)
    if env_error is not None:
        result.had_error = True
        result.error_code = str(env_error.payload.get("code") or "unknown")
        result.error_message = str(env_error.payload.get("message") or "")
        return result

    validation = validate_download_output_dir(
        default_output_dir,
        require_absolute=True,
        require_existing=True,
    )
    result.target_dir = validation.normalized_path

    candidates = await videos_repo.list_videos_by_ids(db, video_ids)
    candidate_map = {str(item.get("video_id")): item for item in candidates}

    for video_id in video_ids:
        video = candidate_map.get(video_id)
        if not video:
            result.missing_count += 1
            continue
        try:
            enqueue_result = await downloads_repo.create_download_job(
                db,
                video_id=str(video["video_id"]),
                video_title=str(video.get("title") or video["video_id"]),
                quality=str(quality),
                overwrite=bool(overwrite),
                target_dir=result.target_dir,
            )
        except Exception:
            result.failed_count += 1
            continue

        if bool(enqueue_result.get("created")):
            result.created_count += 1
        elif bool(enqueue_result.get("duplicate")):
            result.duplicate_count += 1
        else:
            result.failed_count += 1

    return result


async def resolve_download_file_target(
    db: aiosqlite.Connection,
    *,
    filename: str,
    default_download_dir: str,
    job_id: int | None = None,
) -> DownloadFileTargetResult:
    safe_name = Path(filename).name
    if safe_name != filename:
        return DownloadFileTargetResult(
            ok=False,
            code="invalid_filename",
            message="invalid filename",
        )

    target_base = Path(default_download_dir)
    if job_id is not None:
        job = await downloads_repo.get_download_job(db, int(job_id))
        if not job:
            return DownloadFileTargetResult(
                ok=False,
                code="download_job_not_found",
                message="download job not found",
            )
        raw_target_dir = str(job.get("target_dir") or "").strip() or default_download_dir
        try:
            target_base = Path(raw_target_dir).expanduser().resolve(strict=False)
        except OSError:
            return DownloadFileTargetResult(
                ok=False,
                code="download_dir_not_found",
                message="download directory not found",
            )

    target = target_base / safe_name
    if not target.exists() or not target.is_file():
        return DownloadFileTargetResult(
            ok=False,
            code="download_file_not_found",
            message="download file not found",
        )

    return DownloadFileTargetResult(ok=True, target=target)
