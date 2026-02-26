from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import random
import time
from typing import Any

from youtube_transcript_api._errors import (  # pyright: ignore[reportMissingImports]
    AgeRestricted,
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeDataUnparsable,
    YouTubeRequestFailed,
)

from app import repository
from app.state import AppState

logger = logging.getLogger(__name__)


class TranscriptErrorCategory(str, Enum):
    NO_SUBTITLE = "no_subtitle"
    HARD_THROTTLE = "hard_throttle"
    RETRYABLE_TRANSIENT = "retryable_transient"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"


def _extract_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return None


def _is_hard_throttle_error(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    if status_code in {403, 429}:
        return True
    message = str(exc).lower()
    markers = [
        " 403",
        " 429",
        "forbidden",
        "too many requests",
        "rate limit",
        "quota",
    ]
    return any(marker in message for marker in markers)


def _classify_transcript_error(exc: Exception) -> TranscriptErrorCategory:
    if isinstance(exc, (NoTranscriptFound, TranscriptsDisabled)):
        return TranscriptErrorCategory.NO_SUBTITLE
    if isinstance(exc, (RequestBlocked, IpBlocked)):
        return TranscriptErrorCategory.HARD_THROTTLE
    if isinstance(exc, (InvalidVideoId, VideoUnavailable, AgeRestricted, VideoUnplayable)):
        return TranscriptErrorCategory.NON_RETRYABLE_FAILURE
    if isinstance(exc, asyncio.TimeoutError):
        return TranscriptErrorCategory.RETRYABLE_TRANSIENT
    if isinstance(exc, (YouTubeRequestFailed, YouTubeDataUnparsable, CouldNotRetrieveTranscript)):
        return TranscriptErrorCategory.RETRYABLE_TRANSIENT
    if _is_hard_throttle_error(exc):
        return TranscriptErrorCategory.HARD_THROTTLE
    return TranscriptErrorCategory.RETRYABLE_TRANSIENT


def _compute_retry_delay_seconds(base_delay: int, max_delay: int, retry_count: int) -> int:
    safe_base = max(1, int(base_delay))
    safe_max = max(safe_base, int(max_delay))
    safe_retry = max(0, int(retry_count))
    delay = safe_base
    for _ in range(safe_retry):
        delay *= 2
        if delay >= safe_max:
            return safe_max
    return delay


def _compute_hard_cooldown_seconds(base_seconds: int, max_seconds: int, hard_error_count: int) -> int:
    safe_base = max(1, int(base_seconds))
    safe_max = max(safe_base, int(max_seconds))
    safe_count = max(1, int(hard_error_count))
    cooldown = safe_base * (2 ** (safe_count - 1))
    return min(cooldown, safe_max)


def _parse_cooldown(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _compute_jittered_interval_seconds(
    base_interval_seconds: int,
    adaptive_factor: float,
    jitter_ratio: float,
) -> float:
    base = max(1.0, float(base_interval_seconds))
    factor = max(1.0, float(adaptive_factor))
    ratio = max(0.0, min(0.5, float(jitter_ratio)))
    effective = base * factor
    if ratio <= 0.0:
        return effective
    return effective * random.uniform(1.0 - ratio, 1.0 + ratio)


async def _wait_until(monotonic_deadline: float) -> None:
    while True:
        now = time.monotonic()
        remaining = monotonic_deadline - now
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 0.5))


@dataclass(slots=True)
class TranscriptGuardState:
    adaptive_factor: float = 1.0
    cooldown_until: datetime | None = None
    consecutive_hard_errors: int = 0
    consecutive_successes: int = 0

    @classmethod
    def from_repository(cls, payload: dict[str, Any]) -> "TranscriptGuardState":
        return cls(
            adaptive_factor=max(1.0, float(payload.get("adaptive_factor") or 1.0)),
            cooldown_until=_parse_cooldown(payload.get("cooldown_until")),
            consecutive_hard_errors=max(0, int(payload.get("consecutive_hard_errors") or 0)),
            consecutive_successes=max(0, int(payload.get("consecutive_successes") or 0)),
        )

    def to_repository_payload(self) -> dict[str, Any]:
        return {
            "adaptive_factor": self.adaptive_factor,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "consecutive_hard_errors": self.consecutive_hard_errors,
            "consecutive_successes": self.consecutive_successes,
        }


async def _save_guard_state(state: AppState, guard: TranscriptGuardState) -> None:
    payload = guard.to_repository_payload()
    await repository.save_transcript_guard_state(
        state.db,
        adaptive_factor=float(payload["adaptive_factor"]),
        cooldown_until=payload["cooldown_until"],
        consecutive_hard_errors=int(payload["consecutive_hard_errors"]),
        consecutive_successes=int(payload["consecutive_successes"]),
    )


async def run_transcript_fetcher(state: AppState) -> None:
    fetch_batch_size = max(1, int(state.config.transcript_fetch_batch_size))
    request_interval_seconds = max(1, int(state.config.transcript_request_interval_seconds))
    idle_sleep_seconds = max(1, int(state.config.transcript_idle_sleep_seconds))
    retry_base_delay_seconds = max(1, int(state.config.transcript_retry_base_delay_seconds))
    retry_max_delay_seconds = max(retry_base_delay_seconds, int(state.config.transcript_retry_max_delay_seconds))
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

    persisted = await repository.get_transcript_guard_state(state.db)
    guard = TranscriptGuardState.from_repository(persisted)
    next_request_monotonic_at = 0.0

    logger.info(
        "event=transcript.guard_enabled worker=transcript batch=%s interval=%ss jitter=%.2f adaptive=%s factor=%.2f cooldown_until=%s",
        fetch_batch_size,
        request_interval_seconds,
        jitter_ratio,
        adaptive_enabled,
        guard.adaptive_factor,
        guard.cooldown_until.isoformat() if guard.cooldown_until else "-",
        extra={"event": "transcript.guard_enabled", "worker": "transcript"},
    )

    while True:
        if not await repository.is_worker_enabled(state.db, "transcript"):
            await asyncio.sleep(idle_sleep_seconds)
            continue

        now_utc = datetime.now(timezone.utc)
        if guard.cooldown_until and now_utc < guard.cooldown_until:
            remaining = (guard.cooldown_until - now_utc).total_seconds()
            await asyncio.sleep(min(idle_sleep_seconds, max(1.0, remaining)))
            continue
        if guard.cooldown_until and now_utc >= guard.cooldown_until:
            guard.cooldown_until = None
            guard.consecutive_hard_errors = 0
            await _save_guard_state(state, guard)
            logger.info(
                "event=transcript.cooldown_released worker=transcript",
                extra={"event": "transcript.cooldown_released", "worker": "transcript"},
            )

        try:
            pending = await repository.pop_pending_transcript_videos(state.db, limit=fetch_batch_size)
            if not pending:
                await asyncio.sleep(idle_sleep_seconds)
                continue

            for video in pending:
                if not await repository.is_worker_enabled(state.db, "transcript"):
                    break
                if guard.cooldown_until and datetime.now(timezone.utc) < guard.cooldown_until:
                    break

                video_id = video["video_id"]
                preferred_language = str(video.get("transcript_target_language") or "").strip().lower() or None
                try:
                    await _wait_until(next_request_monotonic_at)
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

                    await repository.save_transcript(
                        state.db,
                        video_id=video_id,
                        raw_text=raw_text,
                        language=language,
                        source_type=source_type,
                        thumbnail_path=None,
                    )

                    guard.consecutive_successes += 1
                    guard.consecutive_hard_errors = 0
                    if adaptive_enabled and guard.consecutive_successes >= recovery_success_window:
                        guard.consecutive_successes = 0
                        guard.adaptive_factor = max(1.0, guard.adaptive_factor * 0.8)
                    await _save_guard_state(state, guard)

                    await _wait_until(next_request_monotonic_at)
                    thumbnail_path: str | None = None
                    try:
                        thumbnail_path = await state.transcript_service.download_thumbnail(
                            video_id,
                            state.config.thumbnail_dir,
                        )
                    except Exception as thumbnail_exc:
                        logger.warning(
                            "Thumbnail download failed. video_id=%s error=%s",
                            video_id,
                            thumbnail_exc,
                        )

                    if thumbnail_path:
                        await repository.update_video_thumbnail(
                            state.db,
                            video_id=video_id,
                            thumbnail_path=thumbnail_path,
                        )
                    interval_after_thumbnail = _compute_jittered_interval_seconds(
                        request_interval_seconds,
                        guard.adaptive_factor if adaptive_enabled else 1.0,
                        jitter_ratio,
                    )
                    next_request_monotonic_at = time.monotonic() + interval_after_thumbnail
                except Exception as exc:
                    error_message = str(exc).strip() or exc.__class__.__name__
                    error_category = _classify_transcript_error(exc)
                    interval_after_error = _compute_jittered_interval_seconds(
                        request_interval_seconds,
                        guard.adaptive_factor if adaptive_enabled else 1.0,
                        jitter_ratio,
                    )
                    next_request_monotonic_at = time.monotonic() + interval_after_error

                    current_retry_count = int(video.get("transcript_retry_count") or 0)
                    next_retry_count = current_retry_count + 1

                    if error_category == TranscriptErrorCategory.NO_SUBTITLE:
                        await repository.mark_no_subtitle(state.db, video_id)
                        guard.consecutive_successes += 1
                        guard.consecutive_hard_errors = 0
                        if adaptive_enabled and guard.consecutive_successes >= recovery_success_window:
                            guard.consecutive_successes = 0
                            guard.adaptive_factor = max(1.0, guard.adaptive_factor * 0.8)
                        await _save_guard_state(state, guard)
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

                        guard.consecutive_hard_errors += 1
                        guard.consecutive_successes = 0
                        guard.adaptive_factor = min(adaptive_max_factor, guard.adaptive_factor * 2.0)
                        cooldown_seconds = _compute_hard_cooldown_seconds(
                            hard_cooldown_base_seconds,
                            hard_cooldown_max_seconds,
                            guard.consecutive_hard_errors,
                        )
                        guard.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
                        next_delay_seconds = max(next_delay_seconds, cooldown_seconds)
                        await _save_guard_state(state, guard)

                        if next_retry_count > retry_max_attempts:
                            await repository.mark_transcript_failed(
                                state.db,
                                video_id=video_id,
                                retry_count=next_retry_count,
                                error_message=error_message,
                            )
                            logger.warning(
                                "event=transcript.hard_throttle_limit worker=transcript video_id=%s retry=%s factor=%.2f error=%s",
                                video_id,
                                next_retry_count,
                                guard.adaptive_factor,
                                error_message,
                                extra={
                                    "event": "transcript.hard_throttle_limit",
                                    "worker": "transcript",
                                    "category": "hard_throttle",
                                },
                            )
                        else:
                            await repository.schedule_transcript_retry(
                                state.db,
                                video_id=video_id,
                                delay_seconds=next_delay_seconds,
                                error_message=error_message,
                            )
                            logger.warning(
                                "event=transcript.hard_throttle worker=transcript video_id=%s retry=%s cooldown=%ss factor=%.2f error=%s",
                                video_id,
                                next_retry_count,
                                cooldown_seconds,
                                guard.adaptive_factor,
                                error_message,
                                extra={
                                    "event": "transcript.hard_throttle",
                                    "worker": "transcript",
                                    "category": "hard_throttle",
                                },
                            )
                        continue

                    guard.consecutive_successes = 0
                    guard.consecutive_hard_errors = 0
                    if adaptive_enabled:
                        guard.adaptive_factor = min(
                            adaptive_max_factor,
                            guard.adaptive_factor * general_error_slowdown_multiplier,
                        )
                    await _save_guard_state(state, guard)

                    if error_category == TranscriptErrorCategory.NON_RETRYABLE_FAILURE:
                        await repository.mark_transcript_failed(
                            state.db,
                            video_id=video_id,
                            retry_count=next_retry_count,
                            error_message=error_message,
                        )
                        logger.warning(
                            "Transcript non-retryable failure. video_id=%s retry=%s factor=%.2f error=%s",
                            video_id,
                            next_retry_count,
                            guard.adaptive_factor,
                            error_message,
                        )
                        continue

                    next_delay_seconds = _compute_retry_delay_seconds(
                        retry_base_delay_seconds,
                        retry_max_delay_seconds,
                        current_retry_count,
                    )
                    if next_retry_count > retry_max_attempts:
                        await repository.mark_transcript_failed(
                            state.db,
                            video_id=video_id,
                            retry_count=next_retry_count,
                            error_message=error_message,
                        )
                        logger.warning(
                            "Transcript retry limit reached. video_id=%s retry=%s factor=%.2f error=%s",
                            video_id,
                            next_retry_count,
                            guard.adaptive_factor,
                            error_message,
                        )
                    else:
                        await repository.schedule_transcript_retry(
                            state.db,
                            video_id=video_id,
                            delay_seconds=next_delay_seconds,
                            error_message=error_message,
                        )
                        logger.warning(
                            "Transcript fetch failed. video_id=%s retry=%s factor=%.2f next_delay=%ss error=%s",
                            video_id,
                            next_retry_count,
                            guard.adaptive_factor,
                            next_delay_seconds,
                            error_message,
                        )
        except Exception:
            logger.exception(
                "event=transcript.worker_loop_failed worker=transcript",
                extra={"event": "transcript.worker_loop_failed", "worker": "transcript"},
            )
            await asyncio.sleep(idle_sleep_seconds)
