from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from app.repositories import manual_transcripts as manual_transcripts_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.services.transcript_guard import (
    TranscriptBreakerState,
    TranscriptErrorCategory,
    TranscriptGuardState,
    _classify_transcript_error,
    _close_breaker,
    _compute_hard_cooldown_seconds,
    _compute_jittered_interval_seconds,
    _open_breaker,
    _save_guard_state,
)
from app.state import AppState

logger = logging.getLogger(__name__)


async def _sleep_with_wake(state: AppState, timeout_seconds: float) -> None:
    safe_timeout = max(0.1, float(timeout_seconds))
    wake_event = getattr(state, "manual_transcript_wake_event", None)
    if not isinstance(wake_event, asyncio.Event):
        await asyncio.sleep(safe_timeout)
        return
    if wake_event.is_set():
        wake_event.clear()
        return
    try:
        await asyncio.wait_for(wake_event.wait(), timeout=safe_timeout)
    except TimeoutError:
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


async def _mark_failed(
    state: AppState,
    *,
    job_id: int,
    video_id: str,
    retry_count: int,
    error_message: str,
    event_reason: str,
) -> None:
    await manual_transcripts_repo.mark_manual_transcript_job_failed(
        state.db,
        job_id=job_id,
        retry_count=retry_count,
        error_message=error_message,
    )
    logger.warning(
        "event=manual_transcript.failure job_id=%s video_id=%s reason=%s retry_count=%s",
        job_id,
        video_id,
        event_reason,
        retry_count,
        extra={"event": "manual_transcript.failure", "category": event_reason},
    )


async def run_manual_transcript_worker(state: AppState) -> None:
    idle_sleep_seconds = max(1, int(state.config.transcript_idle_sleep_seconds))
    request_interval_seconds = max(1, int(state.config.transcript_request_interval_seconds))
    fetch_timeout_seconds = max(1, int(state.config.transcript_fetch_timeout_seconds))
    jitter_ratio = max(0.0, min(0.5, float(state.config.transcript_jitter_ratio)))
    adaptive_enabled = bool(state.config.transcript_adaptive_enabled)
    adaptive_max_factor = max(1.0, float(state.config.transcript_adaptive_max_factor))
    hard_cooldown_base_seconds = max(1, int(state.config.transcript_hard_cooldown_base_seconds))
    hard_cooldown_max_seconds = max(
        hard_cooldown_base_seconds,
        int(state.config.transcript_hard_cooldown_max_seconds),
    )
    channel_hard_cooldown_seconds = max(
        1, int(state.config.transcript_channel_hard_cooldown_seconds)
    )
    half_open_probe_count = max(1, int(state.config.transcript_breaker_half_open_probe_count))
    recovery_success_window = max(1, int(state.config.transcript_recovery_success_window))
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

    persisted = await transcripts_repo.get_transcript_guard_state(state.db)
    guard = TranscriptGuardState.from_repository(persisted)
    guard.half_open_probe_remaining = max(1, guard.half_open_probe_remaining)
    logger.info(
        "event=manual_transcript.worker_started worker=manual_transcript",
        extra={"event": "manual_transcript.worker_started", "worker": "manual_transcript"},
    )

    while True:
        try:
            if not await settings_repo.is_worker_enabled(state.db, "transcript"):
                await _sleep_with_wake(state, idle_sleep_seconds)
                continue

            now_monotonic = time.monotonic()
            if now_monotonic >= next_runtime_recover_monotonic_at:
                await manual_transcripts_repo.recover_stuck_manual_transcript_jobs(
                    state.db,
                    stale_after_seconds=runtime_recover_stale_after_seconds,
                    exclude_job_ids=[active_job_id] if active_job_id else None,
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
                guard.half_open_probe_remaining = half_open_probe_count
                await _save_guard_state(state.db, guard)

            job = await manual_transcripts_repo.claim_next_manual_transcript_job(state.db)
            if job is None:
                await _sleep_with_wake(state, idle_sleep_seconds)
                continue

            active_job_id = int(job["id"])
            video_id = str(job.get("video_id") or "").strip()
            channel_id = str(job.get("channel_id") or "").strip()
            try:
                if not video_id:
                    await manual_transcripts_repo.mark_manual_transcript_job_failed(
                        state.db,
                        job_id=active_job_id,
                        error_message="invalid video_id",
                    )
                    continue

                pipeline_status = str(job.get("pipeline_status") or "").strip().lower()
                if (
                    pipeline_status
                    not in manual_transcripts_repo.MANUAL_TRANSCRIPT_ALLOWED_PIPELINE_STATUSES
                ):
                    await manual_transcripts_repo.mark_manual_transcript_job_skipped(
                        state.db,
                        job_id=active_job_id,
                        reason=f"pipeline_status:{pipeline_status}",
                    )
                    logger.info(
                        "event=manual_transcript.skipped job_id=%s video_id=%s reason=%s",
                        active_job_id,
                        video_id,
                        pipeline_status,
                        extra={"event": "manual_transcript.skipped"},
                    )
                    continue

                if bool(job.get("has_transcript")):
                    await manual_transcripts_repo.mark_manual_transcript_job_skipped(
                        state.db,
                        job_id=active_job_id,
                        reason="has_transcript",
                    )
                    logger.info(
                        "event=manual_transcript.skipped job_id=%s video_id=%s reason=has_transcript",
                        active_job_id,
                        video_id,
                        extra={"event": "manual_transcript.skipped"},
                    )
                    continue

                preferred_language = (
                    str(job.get("transcript_target_language") or "").strip().lower() or None
                )
                if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                    if guard.half_open_probe_remaining <= 0:
                        await _sleep_with_wake(state, idle_sleep_seconds)
                        continue
                    guard.half_open_probe_remaining -= 1
                    await _save_guard_state(state.db, guard)

                await _wait_until(next_request_monotonic_at)
                try:
                    guard.last_channel_id = channel_id or guard.last_channel_id
                    guard.last_channel_attempt_at = datetime.now(UTC)
                    await _save_guard_state(state.db, guard)
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
                    retry_count = int(job.get("retry_count") or 0) + 1
                    error_category = _classify_transcript_error(exc)
                    next_request_monotonic_at = (
                        time.monotonic()
                        + _compute_jittered_interval_seconds(
                            request_interval_seconds,
                            guard.adaptive_factor if adaptive_enabled else 1.0,
                            jitter_ratio,
                        )
                    )
                    if error_category == TranscriptErrorCategory.NO_SUBTITLE:
                        await transcripts_repo.mark_no_subtitle(state.db, video_id)
                        guard.consecutive_successes += 1
                        guard.consecutive_hard_errors = 0
                        if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                            _close_breaker(guard, half_open_probe_count=half_open_probe_count)
                        await _save_guard_state(state.db, guard)
                    elif error_category == TranscriptErrorCategory.HARD_THROTTLE:
                        guard.consecutive_hard_errors += 1
                        guard.consecutive_successes = 0
                        if adaptive_enabled:
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
                    else:
                        guard.consecutive_successes = 0
                        guard.consecutive_hard_errors = 0
                        if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                            _close_breaker(guard, half_open_probe_count=half_open_probe_count)
                        await _save_guard_state(state.db, guard)
                    await _mark_failed(
                        state,
                        job_id=active_job_id,
                        video_id=video_id,
                        retry_count=retry_count,
                        error_message=error_message,
                        event_reason=error_category.value,
                    )
                    continue

                next_request_monotonic_at = time.monotonic() + _compute_jittered_interval_seconds(
                    request_interval_seconds,
                    guard.adaptive_factor if adaptive_enabled else 1.0,
                    jitter_ratio,
                )
                if not raw_text.strip():
                    await _mark_failed(
                        state,
                        job_id=active_job_id,
                        video_id=video_id,
                        retry_count=int(job.get("retry_count") or 0) + 1,
                        error_message="Transcript payload is empty",
                        event_reason="empty_transcript",
                    )
                    continue

                guard.consecutive_successes += 1
                guard.consecutive_hard_errors = 0
                if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                    _close_breaker(
                        guard,
                        half_open_probe_count=half_open_probe_count,
                    )
                if adaptive_enabled and guard.consecutive_successes >= recovery_success_window:
                    guard.consecutive_successes = 0
                    guard.adaptive_factor = max(1.0, guard.adaptive_factor * 0.8)
                await _save_guard_state(state.db, guard)

                await transcripts_repo.save_transcript(
                    state.db,
                    video_id=video_id,
                    raw_text=raw_text,
                    language=language,
                    source_type=source_type,
                    thumbnail_path=None,
                    force_llm_pending=False,
                )
                await manual_transcripts_repo.mark_manual_transcript_job_succeeded(
                    state.db,
                    job_id=active_job_id,
                    language=language,
                    source_type=source_type,
                )
                logger.info(
                    "event=manual_transcript.success job_id=%s video_id=%s source_type=%s",
                    active_job_id,
                    video_id,
                    source_type,
                    extra={"event": "manual_transcript.success"},
                )
            finally:
                active_job_id = None
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=manual_transcript.worker_loop_failed worker=manual_transcript",
                extra={
                    "event": "manual_transcript.worker_loop_failed",
                    "worker": "manual_transcript",
                },
            )
            active_job_id = None
            await _sleep_with_wake(state, idle_sleep_seconds)
