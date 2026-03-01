from __future__ import annotations

import asyncio
import logging
import time

from app import repository
from app.state import AppState

logger = logging.getLogger(__name__)


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


async def run_manual_article_worker(state: AppState) -> None:
    idle_sleep_seconds = max(1, int(state.config.transcript_idle_sleep_seconds))
    request_interval_seconds = max(1, int(state.config.transcript_request_interval_seconds))
    fetch_timeout_seconds = max(1, int(state.config.transcript_fetch_timeout_seconds))
    next_request_monotonic_at = 0.0

    while True:
        try:
            job = await repository.claim_next_manual_article_job(state.db)
            if job is None:
                await _sleep_with_wake(state, idle_sleep_seconds)
                continue

            job_id = int(job["id"])
            video_id = str(job.get("video_id") or "").strip()
            if not video_id:
                await repository.mark_manual_article_job_failed(
                    state.db,
                    job_id=job_id,
                    error_message="invalid video_id",
                )
                logger.warning(
                    "event=manual_article.failure job_id=%s video_id=%s reason=invalid_video_id",
                    job_id,
                    video_id or "-",
                    extra={"event": "manual_article.failure"},
                )
                continue

            pipeline_status = str(job.get("pipeline_status") or "").strip().lower()
            if pipeline_status in repository.MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES:
                await repository.mark_manual_article_job_skipped(
                    state.db,
                    job_id=job_id,
                    reason=f"pipeline_status:{pipeline_status}",
                )
                logger.info(
                    "event=manual_article.success job_id=%s video_id=%s mode=skipped reason=%s",
                    job_id,
                    video_id,
                    pipeline_status,
                    extra={"event": "manual_article.success"},
                )
                continue

            if bool(job.get("has_transcript")):
                updated = await repository.ensure_video_llm_pending_for_manual_article(state.db, video_id)
                await repository.mark_manual_article_job_succeeded(state.db, job_id=job_id)
                llm_wake_event = getattr(state, "llm_wake_event", None)
                if updated > 0 and isinstance(llm_wake_event, asyncio.Event):
                    llm_wake_event.set()
                logger.info(
                    "event=manual_article.success job_id=%s video_id=%s mode=transcript_reuse llm_pending_set=%s",
                    job_id,
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
                await repository.force_mark_video_transcript_failed_for_manual_article(
                    state.db,
                    video_id=video_id,
                    retry_count=current_retry_count + 1,
                    error_message=error_message,
                )
                await repository.mark_manual_article_job_failed(
                    state.db,
                    job_id=job_id,
                    error_message=error_message,
                )
                logger.warning(
                    "event=manual_article.failure job_id=%s video_id=%s reason=fetch_failed error=%s",
                    job_id,
                    video_id,
                    error_message,
                    extra={"event": "manual_article.failure"},
                )
                continue

            next_request_monotonic_at = time.monotonic() + request_interval_seconds
            if not raw_text.strip():
                error_message = "Transcript payload is empty"
                current_retry_count = int(job.get("transcript_retry_count") or 0)
                await repository.force_mark_video_transcript_failed_for_manual_article(
                    state.db,
                    video_id=video_id,
                    retry_count=current_retry_count + 1,
                    error_message=error_message,
                )
                await repository.mark_manual_article_job_failed(
                    state.db,
                    job_id=job_id,
                    error_message=error_message,
                )
                logger.warning(
                    "event=manual_article.failure job_id=%s video_id=%s reason=empty_transcript",
                    job_id,
                    video_id,
                    extra={"event": "manual_article.failure"},
                )
                continue

            await repository.save_transcript(
                state.db,
                video_id=video_id,
                raw_text=raw_text,
                language=language,
                source_type=source_type,
                thumbnail_path=None,
            )
            await repository.mark_manual_article_job_succeeded(state.db, job_id=job_id)
            llm_wake_event = getattr(state, "llm_wake_event", None)
            if isinstance(llm_wake_event, asyncio.Event):
                llm_wake_event.set()
            logger.info(
                "event=manual_article.success job_id=%s video_id=%s mode=fetched source_type=%s",
                job_id,
                video_id,
                source_type,
                extra={"event": "manual_article.success"},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=manual_article.worker_loop_failed worker=manual_article",
                extra={"event": "manual_article.worker_loop_failed", "worker": "manual_article"},
            )
            await _sleep_with_wake(state, idle_sleep_seconds)
