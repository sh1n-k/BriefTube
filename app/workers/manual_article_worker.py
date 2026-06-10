from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from app.repositories import manual_articles as manual_articles_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.services.transcript_guard import (
    TranscriptBreakerState,
    TranscriptErrorCategory,
    TranscriptGuardState,
    _adaptive_decay_rate,
    _classify_transcript_error,
    _close_breaker,
    _compute_hard_cooldown_seconds,
    _compute_jittered_interval_seconds,
    _compute_retry_delay_seconds,
    _open_breaker,
    _save_guard_state,
)
from app.state import AppState
from app.workers.wake_sleep import sleep_with_wake_event

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
    await sleep_with_wake_event(
        state,
        "manual_article_wake_event",
        timeout_seconds,
        min_timeout_seconds=0.1,
    )


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
    exclude_job_ids = (
        [exclude_job_id] if isinstance(exclude_job_id, int) and exclude_job_id > 0 else None
    )
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
    retry_base_delay_seconds = max(1, int(state.config.transcript_retry_base_delay_seconds))
    retry_max_delay_seconds = max(
        retry_base_delay_seconds, int(state.config.transcript_retry_max_delay_seconds)
    )
    retry_max_attempts = max(1, int(state.config.transcript_retry_max_attempts))
    jitter_ratio = max(0.0, min(0.5, float(state.config.transcript_jitter_ratio)))
    adaptive_enabled = bool(state.config.transcript_adaptive_enabled)
    adaptive_max_factor = max(1.0, float(state.config.transcript_adaptive_max_factor))
    hard_cooldown_base_seconds = max(1, int(state.config.transcript_hard_cooldown_base_seconds))
    hard_cooldown_max_seconds = max(
        hard_cooldown_base_seconds,
        int(state.config.transcript_hard_cooldown_max_seconds),
    )
    recovery_success_window = max(1, int(state.config.transcript_recovery_success_window))
    general_error_slowdown_multiplier = max(
        1.0,
        float(state.config.transcript_general_error_slowdown_multiplier),
    )
    channel_hard_cooldown_seconds = max(
        1, int(state.config.transcript_channel_hard_cooldown_seconds)
    )
    half_open_probe_count = max(1, int(state.config.transcript_breaker_half_open_probe_count))
    next_request_monotonic_at = 0.0
    runtime_recover_stale_after_seconds, runtime_recover_check_interval_seconds = (
        _runtime_recover_policy(
            idle_sleep_seconds=idle_sleep_seconds,
            request_interval_seconds=request_interval_seconds,
            fetch_timeout_seconds=fetch_timeout_seconds,
        )
    )
    next_runtime_recover_monotonic_at = 0.0
    active_job_id: int | None = None
    active_video_id = "-"
    persisted = await transcripts_repo.get_transcript_guard_state(state.db)
    guard = TranscriptGuardState.from_repository(persisted)
    guard.half_open_probe_remaining = max(1, guard.half_open_probe_remaining)
    logger.info(
        "event=manual_article.worker_started worker=manual_article",
        extra={"event": "manual_article.worker_started", "worker": "manual_article"},
    )
    while True:
        try:
            if not await settings_repo.is_worker_enabled(state.db, "transcript"):
                await _sleep_with_wake(state, idle_sleep_seconds)
                continue

            now_monotonic = time.monotonic()
            if now_monotonic >= next_runtime_recover_monotonic_at:
                await _recover_stale_running_manual_article_jobs(
                    state,
                    stale_after_seconds=runtime_recover_stale_after_seconds,
                    exclude_job_id=active_job_id,
                )
                next_runtime_recover_monotonic_at = (
                    now_monotonic + runtime_recover_check_interval_seconds
                )

            now_utc = datetime.now(UTC)
            if (
                guard.breaker_state == TranscriptBreakerState.OPEN
                and guard.cooldown_until
                and now_utc < guard.cooldown_until
            ):
                remaining = (guard.cooldown_until - now_utc).total_seconds()
                await _sleep_with_wake(state, min(idle_sleep_seconds, max(1.0, remaining)))
                continue

            if guard.breaker_state == TranscriptBreakerState.OPEN and (
                guard.cooldown_until is None or now_utc >= guard.cooldown_until
            ):
                guard.breaker_state = TranscriptBreakerState.HALF_OPEN
                guard.cooldown_until = None
                guard.half_open_probe_remaining = max(1, half_open_probe_count)
                await _save_guard_state(state.db, guard)
                logger.info(
                    "event=manual_article.breaker_half_open probes=%s",
                    guard.half_open_probe_remaining,
                    extra={"event": "manual_article.breaker_half_open"},
                )

            job = await manual_articles_repo.claim_next_manual_article_job(state.db)
            if job is None:
                await _sleep_with_wake(state, idle_sleep_seconds)
                continue

            try:
                active_job_id = int(job["id"])
                video_id = str(job.get("video_id") or "").strip()
                active_video_id = video_id or "-"
                channel_id = str(job.get("channel_id") or "").strip()

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
                if (
                    pipeline_status
                    in manual_articles_repo.MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES
                ):
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
                    updated = (
                        await manual_articles_repo.ensure_video_llm_pending_for_manual_article(
                            state.db, video_id
                        )
                    )
                    await manual_articles_repo.mark_manual_article_job_succeeded(
                        state.db, job_id=active_job_id
                    )
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

                preferred_language = (
                    str(job.get("transcript_target_language") or "").strip().lower() or None
                )
                await _wait_until(next_request_monotonic_at)
                if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                    if guard.half_open_probe_remaining <= 0:
                        await _sleep_with_wake(state, idle_sleep_seconds)
                        continue
                    guard.half_open_probe_remaining -= 1
                    await _save_guard_state(state.db, guard)
                try:
                    guard.last_channel_id = channel_id or guard.last_channel_id
                    guard.last_channel_attempt_at = datetime.now(UTC)
                    await _save_guard_state(state.db, guard)
                    async with state.transcript_fetch_lock:
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
                    error_message = str(exc).strip() or exc.__class__.__name__
                    current_retry_count = int(job.get("transcript_retry_count") or 0)
                    next_retry_count = current_retry_count + 1
                    error_category = _classify_transcript_error(exc)
                    interval_after_error = _compute_jittered_interval_seconds(
                        request_interval_seconds,
                        guard.adaptive_factor if adaptive_enabled else 1.0,
                        jitter_ratio,
                    )
                    next_request_monotonic_at = time.monotonic() + interval_after_error

                    if error_category == TranscriptErrorCategory.NO_SUBTITLE:
                        await transcripts_repo.mark_no_subtitle(state.db, video_id)
                        guard.consecutive_successes += 1
                        guard.consecutive_hard_errors = 0
                        if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                            _close_breaker(guard, half_open_probe_count=half_open_probe_count)
                        if (
                            adaptive_enabled
                            and guard.consecutive_successes >= recovery_success_window
                        ):
                            guard.consecutive_successes = 0
                            decay = _adaptive_decay_rate(guard.adaptive_factor, adaptive_max_factor)
                            guard.adaptive_factor = max(1.0, guard.adaptive_factor * decay)
                        await _save_guard_state(state.db, guard)
                        await manual_articles_repo.mark_manual_article_job_failed(
                            state.db,
                            job_id=active_job_id,
                            error_message="no_subtitle",
                        )
                        logger.info(
                            "event=manual_article.failure job_id=%s video_id=%s reason=no_subtitle",
                            active_job_id,
                            video_id,
                            extra={"event": "manual_article.failure", "category": "no_subtitle"},
                        )
                        continue

                    if error_category == TranscriptErrorCategory.HARD_THROTTLE:
                        next_delay_seconds = _compute_retry_delay_seconds(
                            retry_base_delay_seconds,
                            retry_max_delay_seconds,
                            current_retry_count,
                        )
                        guard.consecutive_hard_errors += 1
                        guard.consecutive_successes = 0
                        guard.adaptive_factor = min(
                            adaptive_max_factor, guard.adaptive_factor * 2.0
                        )
                        breaker_cooldown_seconds = _compute_hard_cooldown_seconds(
                            hard_cooldown_base_seconds,
                            hard_cooldown_max_seconds,
                            guard.consecutive_hard_errors,
                        )
                        _open_breaker(
                            guard,
                            cooldown_seconds=breaker_cooldown_seconds,
                            half_open_probe_count=half_open_probe_count,
                        )
                        await _save_guard_state(state.db, guard)
                        if channel_id:
                            await transcripts_repo.defer_channel_transcript_retries(
                                state.db,
                                channel_id=channel_id,
                                delay_seconds=channel_hard_cooldown_seconds,
                                exclude_video_id=video_id,
                            )
                        next_delay_seconds = max(
                            next_delay_seconds,
                            breaker_cooldown_seconds,
                            channel_hard_cooldown_seconds,
                        )
                        if next_retry_count > retry_max_attempts:
                            await transcripts_repo.mark_transcript_failed(
                                state.db,
                                video_id=video_id,
                                retry_count=next_retry_count,
                                error_message=error_message,
                            )
                        else:
                            scheduled = await transcripts_repo.schedule_transcript_retry(
                                state.db,
                                video_id=video_id,
                                delay_seconds=next_delay_seconds,
                                error_message=error_message,
                            )
                            if scheduled == 0:
                                await manual_articles_repo.force_mark_video_transcript_failed_for_manual_article(
                                    state.db,
                                    video_id=video_id,
                                    retry_count=next_retry_count,
                                    error_message=error_message,
                                )
                        await manual_articles_repo.mark_manual_article_job_failed(
                            state.db,
                            job_id=active_job_id,
                            error_message=error_message,
                        )
                        logger.warning(
                            "event=manual_article.failure job_id=%s video_id=%s reason=hard_throttle next_retry=%s",
                            active_job_id,
                            video_id,
                            next_retry_count,
                            extra={"event": "manual_article.failure", "category": "hard_throttle"},
                        )
                        continue

                    guard.consecutive_successes = 0
                    guard.consecutive_hard_errors = 0
                    if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                        _close_breaker(guard, half_open_probe_count=half_open_probe_count)
                    if adaptive_enabled:
                        guard.adaptive_factor = min(
                            adaptive_max_factor,
                            guard.adaptive_factor * general_error_slowdown_multiplier,
                        )
                    await _save_guard_state(state.db, guard)

                    if (
                        error_category == TranscriptErrorCategory.RETRYABLE_TRANSIENT
                        and next_retry_count <= retry_max_attempts
                    ):
                        next_delay_seconds = _compute_retry_delay_seconds(
                            retry_base_delay_seconds,
                            retry_max_delay_seconds,
                            current_retry_count,
                        )
                        scheduled = await transcripts_repo.schedule_transcript_retry(
                            state.db,
                            video_id=video_id,
                            delay_seconds=next_delay_seconds,
                            error_message=error_message,
                        )
                        if scheduled == 0:
                            await manual_articles_repo.force_mark_video_transcript_failed_for_manual_article(
                                state.db,
                                video_id=video_id,
                                retry_count=next_retry_count,
                                error_message=error_message,
                            )
                    else:
                        await manual_articles_repo.force_mark_video_transcript_failed_for_manual_article(
                            state.db,
                            video_id=video_id,
                            retry_count=next_retry_count,
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
                        extra={"event": "manual_article.failure", "category": error_category.value},
                    )
                    continue

                interval_after_fetch = _compute_jittered_interval_seconds(
                    request_interval_seconds,
                    guard.adaptive_factor if adaptive_enabled else 1.0,
                    jitter_ratio,
                )
                next_request_monotonic_at = time.monotonic() + interval_after_fetch
                if not raw_text.strip():
                    error_message = "Transcript payload is empty"
                    current_retry_count = int(job.get("transcript_retry_count") or 0)
                    await (
                        manual_articles_repo.force_mark_video_transcript_failed_for_manual_article(
                            state.db,
                            video_id=video_id,
                            retry_count=current_retry_count + 1,
                            error_message=error_message,
                        )
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

                guard.consecutive_successes += 1
                guard.consecutive_hard_errors = 0
                if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                    _close_breaker(guard, half_open_probe_count=half_open_probe_count)
                if adaptive_enabled and guard.consecutive_successes >= recovery_success_window:
                    guard.consecutive_successes = 0
                    decay = _adaptive_decay_rate(guard.adaptive_factor, adaptive_max_factor)
                    guard.adaptive_factor = max(1.0, guard.adaptive_factor * decay)
                await _save_guard_state(state.db, guard)

                await transcripts_repo.save_transcript(
                    state.db,
                    video_id=video_id,
                    raw_text=raw_text,
                    language=language,
                    source_type=source_type,
                    thumbnail_path=None,
                    force_llm_pending=True,
                )
                await manual_articles_repo.mark_manual_article_job_succeeded(
                    state.db, job_id=active_job_id
                )
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
