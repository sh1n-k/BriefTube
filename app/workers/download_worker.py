from __future__ import annotations

import asyncio
import logging
import time

from app import repository
from app.services.downloads import download_video
from app.state import AppState

logger = logging.getLogger(__name__)


async def _run_single_download_job(
    state: AppState,
    job: dict[str, object],
) -> None:
    started_monotonic = time.monotonic()
    job_id = int(job["id"])
    video_id = str(job.get("video_id") or "").strip()
    quality = str(job.get("quality") or repository.DOWNLOAD_QUALITY_DEFAULT)
    overwrite = bool(int(job.get("overwrite") or 0))
    target_dir = str(job.get("target_dir") or state.config.download_dir).strip() or state.config.download_dir
    base_timeout_seconds = int(state.config.download_timeout_seconds)
    timeout_seconds = max(base_timeout_seconds, 3600) if quality == "2160" else base_timeout_seconds
    logger.info(
        "event=downloads.job_started job_id=%s video_id=%s quality=%s overwrite=%s target_dir=%s timeout_seconds=%s",
        job_id,
        video_id,
        quality,
        overwrite,
        target_dir,
        timeout_seconds,
        extra={"event": "downloads.job_started"},
    )

    try:
        result = await download_video(
            video_id=video_id,
            quality=quality,
            overwrite=overwrite,
            output_dir=target_dir,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        logger.exception(
            "event=downloads.job_runner_exception job_id=%s",
            job_id,
            extra={"event": "downloads.job_runner_exception"},
        )
        await repository.mark_download_job_failed(
            state.db,
            job_id=job_id,
            error_code="exception",
            error_message="download runner raised an exception",
        )
        return

    if result.ok:
        await repository.mark_download_job_succeeded(
            state.db,
            job_id=job_id,
            output_path=result.output_path,
            file_size_bytes=result.file_size_bytes,
        )
        elapsed = max(0.0, time.monotonic() - started_monotonic)
        logger.info(
            "event=downloads.job_succeeded job_id=%s video_id=%s output_path=%s size_bytes=%s elapsed_sec=%.2f",
            job_id,
            video_id,
            result.output_path or "",
            result.file_size_bytes or 0,
            elapsed,
            extra={"event": "downloads.job_succeeded"},
        )
        return

    await repository.mark_download_job_failed(
        state.db,
        job_id=job_id,
        error_code=result.error_code,
        error_message=result.error_message,
    )
    elapsed = max(0.0, time.monotonic() - started_monotonic)
    logger.warning(
        "event=downloads.job_failed job_id=%s video_id=%s error_code=%s elapsed_sec=%.2f",
        job_id,
        video_id,
        result.error_code,
        elapsed,
        extra={"event": "downloads.job_failed", "code": result.error_code},
    )
    logger.debug(
        "event=downloads.job_failed_detail job_id=%s video_id=%s message=%s",
        job_id,
        video_id,
        result.error_message,
        extra={"event": "downloads.job_failed_detail", "code": result.error_code},
    )


async def run_download_worker(state: AppState) -> None:
    max_concurrency = max(1, min(3, int(state.config.download_max_concurrent)))
    active_tasks: set[asyncio.Task[None]] = set()
    logger.info(
        "event=downloads.worker_started max_concurrency=%s output_dir=%s timeout_seconds=%s",
        max_concurrency,
        state.config.download_dir,
        state.config.download_timeout_seconds,
        extra={"event": "downloads.worker_started"},
    )

    while True:
        try:
            while len(active_tasks) < max_concurrency:
                job = await repository.claim_next_download_job(state.db)
                if job is None:
                    break
                task = asyncio.create_task(_run_single_download_job(state, job))
                active_tasks.add(task)
                logger.debug(
                    "event=downloads.worker_claimed job_id=%s active_tasks=%s",
                    job.get("id"),
                    len(active_tasks),
                    extra={"event": "downloads.worker_claimed"},
                )
                task.add_done_callback(lambda done_task: active_tasks.discard(done_task))

            if active_tasks:
                done, _ = await asyncio.wait(
                    active_tasks,
                    timeout=1,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is not None:
                        logger.exception(
                            "event=downloads.worker_task_failed error_type=%s",
                            exc.__class__.__name__,
                            extra={"event": "downloads.worker_task_failed"},
                        )
                continue

            try:
                await asyncio.wait_for(state.download_wake_event.wait(), timeout=2)
                state.download_wake_event.clear()
                logger.debug(
                    "event=downloads.worker_wake_triggered active_tasks=%s",
                    len(active_tasks),
                    extra={"event": "downloads.worker_wake_triggered"},
                )
            except asyncio.TimeoutError:
                continue
        except asyncio.CancelledError:
            for task in active_tasks:
                task.cancel()
            await asyncio.gather(*active_tasks, return_exceptions=True)
            logger.info(
                "event=downloads.worker_stopped active_tasks=%s",
                len(active_tasks),
                extra={"event": "downloads.worker_stopped"},
            )
            raise
        except Exception:
            logger.exception(
                "event=downloads.worker_loop_failed",
                extra={"event": "downloads.worker_loop_failed"},
            )
            await asyncio.sleep(2)
