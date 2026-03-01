from __future__ import annotations

import asyncio
import logging
import time

from app.repositories import manual_articles as manual_articles_repo
from app.repositories import transcripts as transcripts_repo
from app.state import AppState

logger = logging.getLogger(__name__)


def _runtime_recover_policy(
    *,
    idle_sleep_seconds: int,
    request_interval_seconds: int,
    fetch_timeout_seconds: int,
) -> tuple[int, int]:
    stale_after_seconds = max(
        300,
        fetch_timeout_seconds * 6,
        request_interval_seconds * 8,
        idle_sleep_seconds * 12,
    )
    check_interval_seconds = max(30, min(120, stale_after_seconds // 4))
    return stale_after_seconds, check_interval_seconds


async def _sleep_with_wake(state: AppState, timeout_seconds: float) -> None:
    safe_timeout = max(0.1, float(timeout_seconds))
    wake_event = getattr(state, "manual_article_wake_event", None)
    if not isinstance(wake_event, asyncio.Event):
        await asyncio.sleep(safe_timeout)
        return
    if wake_event.is_set():
        wake_event.clear()
        return
    try:
        await asyncio.wait_for(wake_event.wait(), timeout=safe_timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        if wake_event.is_set():
            wake_event.clear()


async def _wait_until(monotonic_deadline: float) -> None:
    while True:
        remaining = monotonic_deadline - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 0.5))


async def _recover_stale_running_manual_article_jobs(
    state: AppState,
    *,
    stale_after_seconds: int,
    exclude_job_id: int | None = None,
) -> None:
    exclude_job_ids = [exclude_job_id] if isinstance(exclude_job_id, int) and exclude_job_id > 0 else None
    recovered = await manual_articles_repo.recover_stuck_manual_article_jobs(
        state.db,
        stale_after_seconds=stale_after_seconds,
        exclude_job_ids=exclude_job_ids,
    )
    if recovered > 0:
        logger.warning(
            "event=manual_article.runtime_recover recovered=%s stale_after_seconds=%s",
            recovered,
            stale_after_seconds,
            extra={"event": "manual_article.runtime_recover"},
        )


async def _finalize_failed_job_on_unhandled_exception(
    state: AppState,
    *,
    job_id: int,
    video_id: str,
    exc: Exception,
) -> None:
    error_message = str(exc).strip() or exc.__class__.__name__
    try:
        await manual_articles_repo.mark_manual_article_job_failed(
            state.db,
            job_id=job_id,
            error_message=f"worker unhandled exception: {error_message}",
        )
    except Exception:
        logger.exception(
            "event=manual_article.failure_finalize_failed job_id=%s video_id=%s",
            job_id,
            video_id or "-",
            extra={"event": "manual_article.failure_finalize_failed"},
        )


async def run_manual_article_worker(state: AppState) -> None:
    idle_sleep_seconds = max(1, int(state.config.transcript_idle_sleep_seconds))
    request_interval_seconds = max(1, int(state.config.transcript_request_interval_seconds))
    fetch_timeout_seconds = max(1, int(state.config.transcript_fetch_timeout_seconds))
    next_request_monotonic_at = 0.0
    runtime_recover_stale_after_seconds, runtime_recover_check_interval_seconds = _runtime_recover_policy(
        idle_sleep_seconds=idle_sleep_seconds,
        request_interval_seconds=request_interval_seconds,
        fetch_timeout_seconds=fetch_timeout_seconds,
    )
    next_runtime_recover_monotonic_at = 0.0
    active_job_id: int | None = None
    active_video_id = "-"

    while True:
        try:
            now_monotonic = time.monotonic()
            if now_monotonic >= next_runtime_recover_monotonic_at:
                await _recover_stale_running_manual_article_jobs(
                    state,
                    stale_after_seconds=runtime_recover_stale_after_seconds,
                    exclude_job_id=active_job_id,
                )
                next_runtime_recover_monotonic_at = now_monotonic + runtime_recover_check_interval_seconds

            job = await manual_articles_repo.claim_next_manual_article_job(state.db)
            if job is None:
                await _sleep_with_wake(state, idle_sleep_seconds)
                continue

            try:
                active_job_id = int(job["id"])
                video_id = str(job.get("video_id") or "").strip()
                active_video_id = video_id or "-"

                if not video_id:
                    await manual_articles_repo.mark_manual_article_job_failed(
                        state.db,
                        job_id=active_job_id,
                        error_message="invalid video_id",
                    )
                    logger.warning(
                        "event=manual_article.failure job_id=%s video_id=%s reason=invalid_video_id",
                        active_job_id,
                        active_video_id,
                        extra={"event": "manual_article.failure"},
                    )
                    continue

                pipeline_status = str(job.get("pipeline_status") or "").strip().lower()
                if pipeline_status in manual_articles_repo.MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES:
                    await manual_articles_repo.mark_manual_article_job_skipped(
                        state.db,
                        job_id=active_job_id,
                        reason=f"pipeline_status:{pipeline_status}",
                    )
                    logger.info(
                        "event=manual_article.success job_id=%s video_id=%s mode=skipped reason=%s",
                        active_job_id,
                        video_id,
                        pipeline_status,
                        extra={"event": "manual_article.success"},
                    )
                    continue

                if bool(job.get("has_transcript")):
                    updated = await manual_articles_repo.ensure_video_llm_pending_for_manual_article(state.db, video_id)
                    await manual_articles_repo.mark_manual_article_job_succeeded(state.db, job_id=active_job_id)
                    llm_wake_event = getattr(state, "llm_wake_event", None)
                    if updated > 0 and isinstance(llm_wake_event, asyncio.Event):
                        llm_wake_event.set()
                    logger.info(
                        "event=manual_article.success job_id=%s video_id=%s mode=transcript_reuse llm_pending_set=%s",
                        active_job_id,
                        video_id,
                        updated,
                        extra={"event": "manual_article.success"},
                    )
                    continue

                preferred_language = str(job.get("transcript_target_language") or "").strip().lower() or None
                await _wait_until(next_request_monotonic_at)
                try:
                    raw_text, language, source_type = await asyncio.wait_for(
                        state.transcript_service.fetch_transcript(
                            video_id,
                            preferred_language=preferred_language,
                        ),
                        timeout=fetch_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    next_request_monotonic_at = time.monotonic() + request_interval_seconds
                    error_message = str(exc).strip() or exc.__class__.__name__
                    current_retry_count = int(job.get("transcript_retry_count") or 0)
                    await manual_articles_repo.force_mark_video_transcript_failed_for_manual_article(
                        state.db,
                        video_id=video_id,
                        retry_count=current_retry_count + 1,
                        error_message=error_message,
                    )
                    await manual_articles_repo.mark_manual_article_job_failed(
                        state.db,
                        job_id=active_job_id,
                        error_message=error_message,
                    )
                    logger.warning(
                        "event=manual_article.failure job_id=%s video_id=%s reason=fetch_failed error=%s",
                        active_job_id,
                        video_id,
                        error_message,
                        extra={"event": "manual_article.failure"},
                    )
                    continue

                next_request_monotonic_at = time.monotonic() + request_interval_seconds
                if not raw_text.strip():
                    error_message = "Transcript payload is empty"
                    current_retry_count = int(job.get("transcript_retry_count") or 0)
                    await manual_articles_repo.force_mark_video_transcript_failed_for_manual_article(
                        state.db,
                        video_id=video_id,
                        retry_count=current_retry_count + 1,
                        error_message=error_message,
                    )
                    await manual_articles_repo.mark_manual_article_job_failed(
                        state.db,
                        job_id=active_job_id,
                        error_message=error_message,
                    )
                    logger.warning(
                        "event=manual_article.failure job_id=%s video_id=%s reason=empty_transcript",
                        active_job_id,
                        video_id,
                        extra={"event": "manual_article.failure"},
                    )
                    continue

                await transcripts_repo.save_transcript(
                    state.db,
                    video_id=video_id,
                    raw_text=raw_text,
                    language=language,
                    source_type=source_type,
                    thumbnail_path=None,
                    force_llm_pending=True,
                )
                await manual_articles_repo.mark_manual_article_job_succeeded(state.db, job_id=active_job_id)
                llm_wake_event = getattr(state, "llm_wake_event", None)
                if isinstance(llm_wake_event, asyncio.Event):
                    llm_wake_event.set()
                logger.info(
                    "event=manual_article.success job_id=%s video_id=%s mode=fetched source_type=%s",
                    active_job_id,
                    video_id,
                    source_type,
                    extra={"event": "manual_article.success"},
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if active_job_id is not None:
                    await _finalize_failed_job_on_unhandled_exception(
                        state,
                        job_id=active_job_id,
                        video_id=active_video_id,
                        exc=exc,
                    )
                logger.exception(
                    "event=manual_article.failure job_id=%s video_id=%s reason=unhandled_exception",
                    active_job_id if active_job_id is not None else "-",
                    active_video_id,
                    extra={"event": "manual_article.failure"},
                )
            finally:
                active_job_id = None
                active_video_id = "-"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=manual_article.worker_loop_failed worker=manual_article",
                extra={"event": "manual_article.worker_loop_failed", "worker": "manual_article"},
            )
            await _sleep_with_wake(state, idle_sleep_seconds)
