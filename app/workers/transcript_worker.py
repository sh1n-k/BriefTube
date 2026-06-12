from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime

from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.services.transcript_guard import (
    TranscriptBreakerState,
    TranscriptErrorCategory,
    _classify_transcript_error,
    _compute_jittered_interval_seconds,
    _compute_retry_delay_seconds,
    _wait_until,
    claim_transcript_fetch_permit,
    mark_transcript_guard_half_open,
    read_transcript_guard,
    record_transcript_guard_general_failure,
    record_transcript_guard_half_open_retryable_failure,
    record_transcript_guard_hard_throttle,
    record_transcript_guard_success,
    transcript_guard_mutation,
)
from app.state import AppState

logger = logging.getLogger(__name__)


async def _recover_runtime_stuck_transcript_jobs(db) -> int:
    recovered = await transcripts_repo.recover_stuck_transcript_jobs(db)
    if recovered > 0:
        logger.warning(
            "event=transcript.runtime_recover worker=transcript recovered=%s",
            recovered,
            extra={"event": "transcript.runtime_recover", "worker": "transcript"},
        )
    return recovered


async def run_transcript_fetcher(state: AppState) -> None:
    worker_lock = getattr(state, "transcript_worker_lock", None)
    if worker_lock is not None and worker_lock.locked():
        logger.warning(
            "event=transcript.worker_already_running worker=transcript",
            extra={"event": "transcript.worker_already_running", "worker": "transcript"},
        )
        return

    fetch_batch_size = max(1, int(state.config.transcript_fetch_batch_size))
    request_interval_seconds = max(1, int(state.config.transcript_request_interval_seconds))
    idle_sleep_seconds = max(1, int(state.config.transcript_idle_sleep_seconds))
    retry_base_delay_seconds = max(1, int(state.config.transcript_retry_base_delay_seconds))
    retry_max_delay_seconds = max(
        retry_base_delay_seconds, int(state.config.transcript_retry_max_delay_seconds)
    )
    retry_max_attempts = max(1, int(state.config.transcript_retry_max_attempts))
    fetch_timeout_seconds = max(1, int(state.config.transcript_fetch_timeout_seconds))
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
    channel_min_interval_seconds = max(0, int(state.config.transcript_channel_min_interval_seconds))
    channel_pick_lookahead = max(
        fetch_batch_size, int(state.config.transcript_channel_pick_lookahead)
    )
    channel_hard_cooldown_seconds = max(
        1, int(state.config.transcript_channel_hard_cooldown_seconds)
    )
    half_open_probe_count = max(1, int(state.config.transcript_breaker_half_open_probe_count))
    runtime_recovery_pending = True

    db = state.db
    guard_cm = worker_lock if worker_lock is not None else contextlib.nullcontext()
    async with guard_cm:
        startup_guard = await read_transcript_guard(state, db)
        next_request_monotonic_at = 0.0

        logger.info(
            (
                "event=transcript.guard_enabled worker=transcript batch=%s interval=%ss jitter=%.2f "
                "adaptive=%s factor=%.2f breaker=%s cooldown_until=%s"
            ),
            fetch_batch_size,
            request_interval_seconds,
            jitter_ratio,
            adaptive_enabled,
            startup_guard.adaptive_factor,
            startup_guard.breaker_state.value,
            startup_guard.cooldown_until.isoformat() if startup_guard.cooldown_until else "-",
            extra={"event": "transcript.guard_enabled", "worker": "transcript"},
        )

        while True:
            if not await settings_repo.is_worker_enabled(db, "transcript"):
                await asyncio.sleep(idle_sleep_seconds)
                continue

            if runtime_recovery_pending:
                await _recover_runtime_stuck_transcript_jobs(db)
                runtime_recovery_pending = False

            guard = await read_transcript_guard(state, db)
            now_utc = datetime.now(UTC)
            if (
                guard.breaker_state == TranscriptBreakerState.OPEN
                and guard.cooldown_until
                and now_utc < guard.cooldown_until
            ):
                remaining = (guard.cooldown_until - now_utc).total_seconds()
                await asyncio.sleep(min(idle_sleep_seconds, max(1.0, remaining)))
                continue

            if guard.breaker_state == TranscriptBreakerState.OPEN and (
                guard.cooldown_until is None or now_utc >= guard.cooldown_until
            ):
                async with transcript_guard_mutation(state, db) as guard:
                    if guard.breaker_state == TranscriptBreakerState.OPEN and (
                        guard.cooldown_until is None or now_utc >= guard.cooldown_until
                    ):
                        mark_transcript_guard_half_open(
                            guard,
                            half_open_probe_count=half_open_probe_count,
                        )
                logger.info(
                    "event=transcript.breaker_half_open worker=transcript probes=%s",
                    guard.half_open_probe_remaining,
                    extra={"event": "transcript.breaker_half_open", "worker": "transcript"},
                )
                guard = await read_transcript_guard(state, db)

            avoid_channel_id: str | None = None
            remaining_channel_wait = 0.0
            if (
                channel_min_interval_seconds > 0
                and guard.last_channel_id
                and guard.last_channel_attempt_at is not None
            ):
                elapsed = (now_utc - guard.last_channel_attempt_at).total_seconds()
                remaining = float(channel_min_interval_seconds) - elapsed
                if remaining > 0:
                    avoid_channel_id = guard.last_channel_id
                    remaining_channel_wait = remaining

            try:
                logger.debug(
                    "event=transcript.polling_pending worker=transcript breaker=%s avoid=%s",
                    guard.breaker_state.value,
                    avoid_channel_id or "-",
                    extra={"event": "transcript.polling_pending", "worker": "transcript"},
                )
                pending = await transcripts_repo.pop_pending_transcript_videos(
                    db,
                    limit=fetch_batch_size,
                    lookahead=channel_pick_lookahead,
                    avoid_channel_id=avoid_channel_id,
                )
                if avoid_channel_id and remaining_channel_wait > 0:
                    pending = [
                        v
                        for v in pending
                        if str(v.get("channel_id") or "").strip() != avoid_channel_id
                    ]
                if not pending:
                    sleep_seconds = idle_sleep_seconds
                    if remaining_channel_wait > 0:
                        sleep_seconds = min(sleep_seconds, max(1.0, remaining_channel_wait))
                    logger.debug(
                        "event=transcript.no_pending worker=transcript sleep=%.1fs avoid_channel=%s",
                        sleep_seconds,
                        avoid_channel_id or "-",
                        extra={"event": "transcript.no_pending", "worker": "transcript"},
                    )
                    await asyncio.sleep(sleep_seconds)
                    continue

                for video in pending:
                    video_id = video["video_id"]
                    channel_id = str(video.get("channel_id") or "").strip()
                    preferred_language = (
                        str(video.get("transcript_target_language") or "").strip().lower() or None
                    )

                    try:
                        await _wait_until(next_request_monotonic_at)
                        permit, guard = await claim_transcript_fetch_permit(
                            state,
                            db,
                            channel_id=channel_id,
                            half_open_probe_count=half_open_probe_count,
                        )
                        if not permit:
                            break
                        marked = await transcripts_repo.mark_transcript_processing(db, video_id)
                        if marked == 0:
                            continue
                        async with state.transcript_fetch_lock:
                            raw_text, language, source_type = await asyncio.wait_for(
                                state.transcript_service.fetch_transcript(
                                    video_id,
                                    preferred_language=preferred_language,
                                ),
                                timeout=fetch_timeout_seconds,
                            )
                        interval_after_fetch = _compute_jittered_interval_seconds(
                            request_interval_seconds,
                            guard.adaptive_factor if adaptive_enabled else 1.0,
                            jitter_ratio,
                        )
                        next_request_monotonic_at = time.monotonic() + interval_after_fetch

                        if not raw_text.strip():
                            raise ValueError("Transcript payload is empty")

                        await transcripts_repo.save_transcript(
                            db,
                            video_id=video_id,
                            raw_text=raw_text,
                            language=language,
                            source_type=source_type,
                            thumbnail_path=None,
                        )

                        logger.info(
                            "event=transcript.fetch_succeeded worker=transcript video_id=%s language=%s source=%s",
                            video_id,
                            language or "-",
                            source_type,
                            extra={"event": "transcript.fetch_succeeded", "worker": "transcript"},
                        )
                        async with transcript_guard_mutation(state, db) as guard:
                            record_transcript_guard_success(
                                guard,
                                adaptive_enabled=adaptive_enabled,
                                recovery_success_window=recovery_success_window,
                                adaptive_max_factor=adaptive_max_factor,
                                half_open_probe_count=half_open_probe_count,
                            )

                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        error_message = str(exc).strip() or exc.__class__.__name__
                        error_category = _classify_transcript_error(exc)

                        current_retry_count = int(video.get("transcript_retry_count") or 0)
                        next_retry_count = current_retry_count + 1

                        interval_after_error = _compute_jittered_interval_seconds(
                            request_interval_seconds,
                            guard.adaptive_factor if adaptive_enabled else 1.0,
                            jitter_ratio,
                        )
                        next_request_monotonic_at = time.monotonic() + interval_after_error

                        if error_category == TranscriptErrorCategory.NO_SUBTITLE:
                            await transcripts_repo.mark_no_subtitle(db, video_id)
                            async with transcript_guard_mutation(state, db) as guard:
                                record_transcript_guard_success(
                                    guard,
                                    adaptive_enabled=adaptive_enabled,
                                    recovery_success_window=recovery_success_window,
                                    adaptive_max_factor=adaptive_max_factor,
                                    half_open_probe_count=half_open_probe_count,
                                )
                            logger.info(
                                "event=transcript.no_subtitle worker=transcript video_id=%s",
                                video_id,
                                extra={"event": "transcript.no_subtitle", "worker": "transcript"},
                            )
                            continue

                        if error_category == TranscriptErrorCategory.HARD_THROTTLE:
                            next_delay_seconds = _compute_retry_delay_seconds(
                                retry_base_delay_seconds,
                                retry_max_delay_seconds,
                                current_retry_count,
                            )

                            async with transcript_guard_mutation(state, db) as guard:
                                breaker_cooldown_seconds = record_transcript_guard_hard_throttle(
                                    guard,
                                    adaptive_max_factor=adaptive_max_factor,
                                    hard_cooldown_base_seconds=hard_cooldown_base_seconds,
                                    hard_cooldown_max_seconds=hard_cooldown_max_seconds,
                                    half_open_probe_count=half_open_probe_count,
                                )
                                post_throttle_factor = guard.adaptive_factor
                            next_delay_seconds = max(
                                next_delay_seconds,
                                breaker_cooldown_seconds,
                                channel_hard_cooldown_seconds,
                            )

                            if channel_id:
                                await transcripts_repo.defer_channel_transcript_retries(
                                    db,
                                    channel_id=channel_id,
                                    delay_seconds=channel_hard_cooldown_seconds,
                                    exclude_video_id=video_id,
                                )

                            if next_retry_count > retry_max_attempts:
                                await transcripts_repo.mark_transcript_failed(
                                    db,
                                    video_id=video_id,
                                    retry_count=next_retry_count,
                                    error_message=error_message,
                                )
                                logger.warning(
                                    "event=transcript.hard_throttle_limit worker=transcript video_id=%s retry=%s factor=%.2f error=%s",
                                    video_id,
                                    next_retry_count,
                                    post_throttle_factor,
                                    error_message,
                                    extra={
                                        "event": "transcript.hard_throttle_limit",
                                        "worker": "transcript",
                                        "category": "hard_throttle",
                                    },
                                )
                            else:
                                await transcripts_repo.schedule_transcript_retry(
                                    db,
                                    video_id=video_id,
                                    delay_seconds=next_delay_seconds,
                                    error_message=error_message,
                                )
                                logger.warning(
                                    (
                                        "event=transcript.hard_throttle worker=transcript video_id=%s retry=%s "
                                        "cooldown=%ss channel_cooldown=%ss factor=%.2f error=%s"
                                    ),
                                    video_id,
                                    next_retry_count,
                                    breaker_cooldown_seconds,
                                    channel_hard_cooldown_seconds,
                                    post_throttle_factor,
                                    error_message,
                                    extra={
                                        "event": "transcript.hard_throttle",
                                        "worker": "transcript",
                                        "category": "hard_throttle",
                                    },
                                )
                            continue

                        if (
                            guard.breaker_state == TranscriptBreakerState.HALF_OPEN
                            and error_category == TranscriptErrorCategory.RETRYABLE_TRANSIENT
                        ):
                            async with transcript_guard_mutation(state, db) as guard:
                                probe_cooldown_seconds = record_transcript_guard_half_open_retryable_failure(
                                    guard,
                                    adaptive_enabled=adaptive_enabled,
                                    adaptive_max_factor=adaptive_max_factor,
                                    general_error_slowdown_multiplier=general_error_slowdown_multiplier,
                                    hard_cooldown_base_seconds=hard_cooldown_base_seconds,
                                    hard_cooldown_max_seconds=hard_cooldown_max_seconds,
                                    half_open_probe_count=half_open_probe_count,
                                )
                                post_probe_factor = guard.adaptive_factor
                            next_delay_seconds = max(
                                _compute_retry_delay_seconds(
                                    retry_base_delay_seconds,
                                    retry_max_delay_seconds,
                                    current_retry_count,
                                ),
                                probe_cooldown_seconds,
                            )
                            if next_retry_count > retry_max_attempts:
                                await transcripts_repo.mark_transcript_failed(
                                    db,
                                    video_id=video_id,
                                    retry_count=next_retry_count,
                                    error_message=error_message,
                                )
                            else:
                                await transcripts_repo.schedule_transcript_retry(
                                    db,
                                    video_id=video_id,
                                    delay_seconds=next_delay_seconds,
                                    error_message=error_message,
                                )
                            logger.warning(
                                (
                                    "event=transcript.half_open_retryable_failure worker=transcript video_id=%s "
                                    "retry=%s cooldown=%ss factor=%.2f error=%s"
                                ),
                                video_id,
                                next_retry_count,
                                probe_cooldown_seconds,
                                post_probe_factor,
                                error_message,
                                extra={
                                    "event": "transcript.half_open_retryable_failure",
                                    "worker": "transcript",
                                    "category": "retryable_transient",
                                },
                            )
                            continue

                        async with transcript_guard_mutation(state, db) as guard:
                            record_transcript_guard_general_failure(
                                guard,
                                adaptive_enabled=adaptive_enabled,
                                adaptive_max_factor=adaptive_max_factor,
                                general_error_slowdown_multiplier=general_error_slowdown_multiplier,
                                half_open_probe_count=half_open_probe_count,
                            )
                            post_general_factor = guard.adaptive_factor

                        if error_category == TranscriptErrorCategory.NON_RETRYABLE_FAILURE:
                            await transcripts_repo.mark_transcript_failed(
                                db,
                                video_id=video_id,
                                retry_count=next_retry_count,
                                error_message=error_message,
                            )
                            logger.warning(
                                "Transcript non-retryable failure. video_id=%s retry=%s factor=%.2f error=%s",
                                video_id,
                                next_retry_count,
                                post_general_factor,
                                error_message,
                            )
                            continue

                        next_delay_seconds = _compute_retry_delay_seconds(
                            retry_base_delay_seconds,
                            retry_max_delay_seconds,
                            current_retry_count,
                        )
                        if next_retry_count > retry_max_attempts:
                            await transcripts_repo.mark_transcript_failed(
                                db,
                                video_id=video_id,
                                retry_count=next_retry_count,
                                error_message=error_message,
                            )
                            logger.warning(
                                "Transcript retry limit reached. video_id=%s retry=%s factor=%.2f error=%s",
                                video_id,
                                next_retry_count,
                                post_general_factor,
                                error_message,
                            )
                        else:
                            await transcripts_repo.schedule_transcript_retry(
                                db,
                                video_id=video_id,
                                delay_seconds=next_delay_seconds,
                                error_message=error_message,
                            )
                            logger.warning(
                                "Transcript fetch failed. video_id=%s retry=%s factor=%.2f next_delay=%ss error=%s",
                                video_id,
                                next_retry_count,
                                post_general_factor,
                                next_delay_seconds,
                                error_message,
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "event=transcript.worker_loop_failed worker=transcript",
                    extra={"event": "transcript.worker_loop_failed", "worker": "transcript"},
                )
                await asyncio.sleep(idle_sleep_seconds)
