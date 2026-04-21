from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import os
import random
import time
from typing import Any
import uuid

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

from app.database import open_database
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.state import AppState

logger = logging.getLogger(__name__)


class TranscriptErrorCategory(str, Enum):
    NO_SUBTITLE = "no_subtitle"
    HARD_THROTTLE = "hard_throttle"
    RETRYABLE_TRANSIENT = "retryable_transient"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"


class TranscriptBreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


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
        "youtube is blocking requests from your ip",
        "ip has been blocked",
        "request blocked",
        "forbidden",
        "too many requests",
        "rate limit",
        "quota",
        "captcha",
        "unusual traffic",
    ]
    return any(marker in message for marker in markers)


def _classify_transcript_error(exc: Exception) -> TranscriptErrorCategory:
    if isinstance(exc, (NoTranscriptFound, TranscriptsDisabled)):
        return TranscriptErrorCategory.NO_SUBTITLE
    if isinstance(exc, (RequestBlocked, IpBlocked)):
        return TranscriptErrorCategory.HARD_THROTTLE
    if _is_hard_throttle_error(exc):
        return TranscriptErrorCategory.HARD_THROTTLE
    if isinstance(exc, (InvalidVideoId, VideoUnavailable, AgeRestricted, VideoUnplayable)):
        return TranscriptErrorCategory.NON_RETRYABLE_FAILURE
    if isinstance(exc, asyncio.TimeoutError):
        return TranscriptErrorCategory.RETRYABLE_TRANSIENT
    if isinstance(exc, (YouTubeRequestFailed, YouTubeDataUnparsable, CouldNotRetrieveTranscript)):
        return TranscriptErrorCategory.RETRYABLE_TRANSIENT
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


def _adaptive_decay_rate(adaptive_factor: float, adaptive_max_factor: float) -> float:
    high = adaptive_max_factor / 2.0
    mid = adaptive_max_factor / 4.0
    if adaptive_factor >= high:
        return 0.5
    if adaptive_factor >= mid:
        return 0.65
    return 0.8


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
    breaker_state: TranscriptBreakerState = TranscriptBreakerState.CLOSED
    half_open_probe_remaining: int = 1
    last_channel_id: str | None = None
    last_channel_attempt_at: datetime | None = None

    @classmethod
    def from_repository(cls, payload: dict[str, Any]) -> "TranscriptGuardState":
        state_raw = str(payload.get("breaker_state") or TranscriptBreakerState.CLOSED.value).strip().lower()
        if state_raw not in {item.value for item in TranscriptBreakerState}:
            state_raw = TranscriptBreakerState.CLOSED.value
        return cls(
            adaptive_factor=max(1.0, float(payload.get("adaptive_factor") or 1.0)),
            cooldown_until=_parse_cooldown(payload.get("cooldown_until")),
            consecutive_hard_errors=max(0, int(payload.get("consecutive_hard_errors") or 0)),
            consecutive_successes=max(0, int(payload.get("consecutive_successes") or 0)),
            breaker_state=TranscriptBreakerState(state_raw),
            half_open_probe_remaining=max(1, int(payload.get("half_open_probe_remaining") or 1)),
            last_channel_id=str(payload.get("last_channel_id") or "").strip() or None,
            last_channel_attempt_at=_parse_cooldown(payload.get("last_channel_attempt_at")),
        )

    def to_repository_payload(self) -> dict[str, Any]:
        return {
            "adaptive_factor": self.adaptive_factor,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "consecutive_hard_errors": self.consecutive_hard_errors,
            "consecutive_successes": self.consecutive_successes,
            "breaker_state": self.breaker_state.value,
            "half_open_probe_remaining": self.half_open_probe_remaining,
            "last_channel_id": self.last_channel_id,
            "last_channel_attempt_at": (
                self.last_channel_attempt_at.isoformat() if self.last_channel_attempt_at else None
            ),
        }


async def _save_guard_state(db, guard: TranscriptGuardState) -> None:
    payload = guard.to_repository_payload()
    await transcripts_repo.save_transcript_guard_state(
        db,
        adaptive_factor=float(payload["adaptive_factor"]),
        cooldown_until=payload["cooldown_until"],
        consecutive_hard_errors=int(payload["consecutive_hard_errors"]),
        consecutive_successes=int(payload["consecutive_successes"]),
        breaker_state=str(payload["breaker_state"]),
        half_open_probe_remaining=int(payload["half_open_probe_remaining"]),
        last_channel_id=payload["last_channel_id"],
        last_channel_attempt_at=payload["last_channel_attempt_at"],
    )


def _open_breaker(
    guard: TranscriptGuardState,
    *,
    cooldown_seconds: int,
    half_open_probe_count: int,
) -> None:
    guard.breaker_state = TranscriptBreakerState.OPEN
    guard.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(cooldown_seconds)))
    guard.half_open_probe_remaining = max(1, int(half_open_probe_count))


def _close_breaker(guard: TranscriptGuardState, *, half_open_probe_count: int) -> None:
    guard.breaker_state = TranscriptBreakerState.CLOSED
    guard.cooldown_until = None
    guard.half_open_probe_remaining = max(1, int(half_open_probe_count))


def _reopen_breaker_after_half_open_retryable_failure(
    guard: TranscriptGuardState,
    *,
    hard_cooldown_base_seconds: int,
    hard_cooldown_max_seconds: int,
    half_open_probe_count: int,
) -> int:
    preserved_hard_errors = max(1, guard.consecutive_hard_errors)
    cooldown_seconds = _compute_hard_cooldown_seconds(
        hard_cooldown_base_seconds,
        hard_cooldown_max_seconds,
        preserved_hard_errors,
    )
    _open_breaker(
        guard,
        cooldown_seconds=cooldown_seconds,
        half_open_probe_count=half_open_probe_count,
    )
    return cooldown_seconds


async def _lease_heartbeat_loop(
    *,
    db,
    owner_id: str,
    ttl_seconds: int,
    renew_interval_seconds: float,
    stop_event: asyncio.Event,
    lost_event: asyncio.Event,
) -> None:
    safe_interval = max(0.1, float(renew_interval_seconds))
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=safe_interval)
            return
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            return
        try:
            renewed = await transcripts_repo.renew_transcript_worker_lease(
                db,
                owner_id=owner_id,
                ttl_seconds=ttl_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            lost_event.set()
            logger.exception(
                "event=transcript.lease_renew_failed worker=transcript owner=%s",
                owner_id,
                extra={"event": "transcript.lease_renew_failed", "worker": "transcript"},
            )
            return
        if not renewed:
            lost_event.set()
            logger.warning(
                "event=transcript.lease_lost worker=transcript owner=%s",
                owner_id,
                extra={"event": "transcript.lease_lost", "worker": "transcript"},
            )
            return


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
    channel_min_interval_seconds = max(0, int(state.config.transcript_channel_min_interval_seconds))
    channel_pick_lookahead = max(fetch_batch_size, int(state.config.transcript_channel_pick_lookahead))
    channel_hard_cooldown_seconds = max(1, int(state.config.transcript_channel_hard_cooldown_seconds))
    half_open_probe_count = max(1, int(state.config.transcript_breaker_half_open_probe_count))
    lease_enabled = bool(state.config.transcript_worker_lease_enabled)
    lease_ttl_seconds = max(
        5,
        int(state.config.transcript_worker_lease_ttl_seconds),
        fetch_timeout_seconds + 10,
    )
    lease_owner_id = f"transcript-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    lease_held = False
    lease_renew_interval_seconds = max(1.0, lease_ttl_seconds / 3.0)
    runtime_recovery_pending = True
    lease_stop_event = asyncio.Event()
    lease_lost_event = asyncio.Event()
    lease_heartbeat_task: asyncio.Task[None] | None = None

    worker_db = None
    worker_db_task: asyncio.Task | None = asyncio.create_task(
        open_database(state.config.db_path),
        name="transcript_worker_db_open",
    )

    try:
        worker_db = await asyncio.shield(worker_db_task)
        persisted = await transcripts_repo.get_transcript_guard_state(worker_db)
        guard = TranscriptGuardState.from_repository(persisted)
        guard.half_open_probe_remaining = max(1, guard.half_open_probe_remaining)
        next_request_monotonic_at = 0.0

        logger.info(
            (
                "event=transcript.guard_enabled worker=transcript batch=%s interval=%ss jitter=%.2f "
                "adaptive=%s factor=%.2f breaker=%s cooldown_until=%s lease_enabled=%s dedicated_db=%s"
            ),
            fetch_batch_size,
            request_interval_seconds,
            jitter_ratio,
            adaptive_enabled,
            guard.adaptive_factor,
            guard.breaker_state.value,
            guard.cooldown_until.isoformat() if guard.cooldown_until else "-",
            lease_enabled,
            worker_db is not state.db,
            extra={"event": "transcript.guard_enabled", "worker": "transcript"},
        )

        while True:
            if lease_enabled and lease_lost_event.is_set():
                if lease_heartbeat_task is not None:
                    lease_stop_event.set()
                    lease_heartbeat_task.cancel()
                    await asyncio.gather(lease_heartbeat_task, return_exceptions=True)
                    lease_heartbeat_task = None
                lease_stop_event = asyncio.Event()
                lease_lost_event = asyncio.Event()
                lease_held = False
                await asyncio.sleep(idle_sleep_seconds)
                continue

            if not await settings_repo.is_worker_enabled(worker_db, "transcript"):
                if lease_enabled and lease_held:
                    if lease_heartbeat_task is not None:
                        lease_stop_event.set()
                        lease_heartbeat_task.cancel()
                        await asyncio.gather(lease_heartbeat_task, return_exceptions=True)
                        lease_heartbeat_task = None
                    lease_stop_event = asyncio.Event()
                    lease_lost_event = asyncio.Event()
                    try:
                        await transcripts_repo.release_transcript_worker_lease(worker_db, lease_owner_id)
                    except Exception:
                        logger.exception(
                            "event=transcript.lease_release_failed worker=transcript owner=%s",
                            lease_owner_id,
                            extra={"event": "transcript.lease_release_failed", "worker": "transcript"},
                        )
                    lease_held = False
                await asyncio.sleep(idle_sleep_seconds)
                continue

            if lease_enabled:
                now_mono = time.monotonic()
                if not lease_held:
                    try:
                        lease_held = await transcripts_repo.acquire_transcript_worker_lease(
                            worker_db,
                            owner_id=lease_owner_id,
                            ttl_seconds=lease_ttl_seconds,
                        )
                    except Exception:
                        logger.exception(
                            "event=transcript.lease_acquire_failed worker=transcript owner=%s",
                            lease_owner_id,
                            extra={"event": "transcript.lease_acquire_failed", "worker": "transcript"},
                        )
                        await asyncio.sleep(idle_sleep_seconds)
                        continue
                    if not lease_held:
                        logger.debug(
                            "event=transcript.lease_not_acquired worker=transcript owner=%s",
                            lease_owner_id,
                            extra={"event": "transcript.lease_not_acquired", "worker": "transcript"},
                        )
                        await asyncio.sleep(idle_sleep_seconds)
                        continue
                    lease_stop_event = asyncio.Event()
                    lease_lost_event = asyncio.Event()
                    lease_heartbeat_task = asyncio.create_task(
                        _lease_heartbeat_loop(
                            db=worker_db,
                            owner_id=lease_owner_id,
                            ttl_seconds=lease_ttl_seconds,
                            renew_interval_seconds=lease_renew_interval_seconds,
                            stop_event=lease_stop_event,
                            lost_event=lease_lost_event,
                        ),
                        name="transcript_lease_heartbeat",
                    )
                    if runtime_recovery_pending:
                        await _recover_runtime_stuck_transcript_jobs(worker_db)
                        runtime_recovery_pending = False
                    logger.info(
                        "event=transcript.lease_acquired worker=transcript owner=%s",
                        lease_owner_id,
                        extra={"event": "transcript.lease_acquired", "worker": "transcript"},
                    )

            now_utc = datetime.now(timezone.utc)
            if guard.breaker_state == TranscriptBreakerState.OPEN and guard.cooldown_until and now_utc < guard.cooldown_until:
                remaining = (guard.cooldown_until - now_utc).total_seconds()
                await asyncio.sleep(min(idle_sleep_seconds, max(1.0, remaining)))
                continue

            if guard.breaker_state == TranscriptBreakerState.OPEN and (
                guard.cooldown_until is None or now_utc >= guard.cooldown_until
            ):
                guard.breaker_state = TranscriptBreakerState.HALF_OPEN
                guard.cooldown_until = None
                guard.half_open_probe_remaining = max(1, half_open_probe_count)
                await _save_guard_state(worker_db, guard)
                logger.info(
                    "event=transcript.breaker_half_open worker=transcript probes=%s",
                    guard.half_open_probe_remaining,
                    extra={"event": "transcript.breaker_half_open", "worker": "transcript"},
                )

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
                    worker_db,
                    limit=fetch_batch_size,
                    lookahead=channel_pick_lookahead,
                    avoid_channel_id=avoid_channel_id,
                )
                if avoid_channel_id and remaining_channel_wait > 0:
                    pending = [
                        v for v in pending
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
                    if not await settings_repo.is_worker_enabled(worker_db, "transcript"):
                        break
                    if lease_enabled and (not lease_held or lease_lost_event.is_set()):
                        break
                    if guard.breaker_state == TranscriptBreakerState.OPEN and guard.cooldown_until:
                        if datetime.now(timezone.utc) < guard.cooldown_until:
                            break

                    video_id = video["video_id"]
                    channel_id = str(video.get("channel_id") or "").strip()
                    preferred_language = str(video.get("transcript_target_language") or "").strip().lower() or None

                    if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                        if guard.half_open_probe_remaining <= 0:
                            break
                        guard.half_open_probe_remaining -= 1
                        await _save_guard_state(worker_db, guard)

                    try:
                        guard.last_channel_id = channel_id or guard.last_channel_id
                        guard.last_channel_attempt_at = datetime.now(timezone.utc)
                        await _save_guard_state(worker_db, guard)
                        marked = await transcripts_repo.mark_transcript_processing(worker_db, video_id)
                        if marked == 0:
                            continue

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

                        await transcripts_repo.save_transcript(
                            worker_db,
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
                        guard.consecutive_successes += 1
                        guard.consecutive_hard_errors = 0
                        if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                            _close_breaker(guard, half_open_probe_count=half_open_probe_count)
                        if adaptive_enabled and guard.consecutive_successes >= recovery_success_window:
                            guard.consecutive_successes = 0
                            decay = _adaptive_decay_rate(guard.adaptive_factor, adaptive_max_factor)
                            guard.adaptive_factor = max(1.0, guard.adaptive_factor * decay)
                        await _save_guard_state(worker_db, guard)

                    except asyncio.CancelledError:
                        raise
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
                            await transcripts_repo.mark_no_subtitle(worker_db, video_id)
                            guard.consecutive_successes += 1
                            guard.consecutive_hard_errors = 0
                            if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
                                _close_breaker(guard, half_open_probe_count=half_open_probe_count)
                            if adaptive_enabled and guard.consecutive_successes >= recovery_success_window:
                                guard.consecutive_successes = 0
                                decay = _adaptive_decay_rate(guard.adaptive_factor, adaptive_max_factor)
                                guard.adaptive_factor = max(1.0, guard.adaptive_factor * decay)
                            await _save_guard_state(worker_db, guard)
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
                            next_delay_seconds = max(
                                next_delay_seconds,
                                breaker_cooldown_seconds,
                                channel_hard_cooldown_seconds,
                            )
                            await _save_guard_state(worker_db, guard)

                            if channel_id:
                                await transcripts_repo.defer_channel_transcript_retries(
                                    worker_db,
                                    channel_id=channel_id,
                                    delay_seconds=channel_hard_cooldown_seconds,
                                    exclude_video_id=video_id,
                                )

                            if next_retry_count > retry_max_attempts:
                                await transcripts_repo.mark_transcript_failed(
                                    worker_db,
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
                                await transcripts_repo.schedule_transcript_retry(
                                    worker_db,
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
                                    guard.adaptive_factor,
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
                            guard.consecutive_successes = 0
                            if adaptive_enabled:
                                guard.adaptive_factor = min(
                                    adaptive_max_factor,
                                    guard.adaptive_factor * general_error_slowdown_multiplier,
                                )
                            probe_cooldown_seconds = _reopen_breaker_after_half_open_retryable_failure(
                                guard,
                                hard_cooldown_base_seconds=hard_cooldown_base_seconds,
                                hard_cooldown_max_seconds=hard_cooldown_max_seconds,
                                half_open_probe_count=half_open_probe_count,
                            )
                            await _save_guard_state(worker_db, guard)
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
                                    worker_db,
                                    video_id=video_id,
                                    retry_count=next_retry_count,
                                    error_message=error_message,
                                )
                            else:
                                await transcripts_repo.schedule_transcript_retry(
                                    worker_db,
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
                                guard.adaptive_factor,
                                error_message,
                                extra={
                                    "event": "transcript.half_open_retryable_failure",
                                    "worker": "transcript",
                                    "category": "retryable_transient",
                                },
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
                        await _save_guard_state(worker_db, guard)

                        if error_category == TranscriptErrorCategory.NON_RETRYABLE_FAILURE:
                            await transcripts_repo.mark_transcript_failed(
                                worker_db,
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
                            await transcripts_repo.mark_transcript_failed(
                                worker_db,
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
                            await transcripts_repo.schedule_transcript_retry(
                                worker_db,
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
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "event=transcript.worker_loop_failed worker=transcript",
                    extra={"event": "transcript.worker_loop_failed", "worker": "transcript"},
                )
                await asyncio.sleep(idle_sleep_seconds)
    finally:
        if lease_heartbeat_task is not None:
            lease_stop_event.set()
            lease_heartbeat_task.cancel()
            await asyncio.gather(lease_heartbeat_task, return_exceptions=True)
        if worker_db is None and worker_db_task is not None:
            if not worker_db_task.done():
                try:
                    worker_db = await asyncio.shield(worker_db_task)
                except Exception:
                    worker_db = None
            elif not worker_db_task.cancelled():
                try:
                    worker_db = worker_db_task.result()
                except Exception:
                    worker_db = None
        if lease_enabled and lease_held and worker_db is not None:
            try:
                await asyncio.shield(
                    transcripts_repo.release_transcript_worker_lease(worker_db, lease_owner_id)
                )
            except Exception:
                logger.exception(
                    "event=transcript.lease_release_failed worker=transcript owner=%s",
                    lease_owner_id,
                    extra={"event": "transcript.lease_release_failed", "worker": "transcript"},
                )
        if worker_db is not None:
            await asyncio.shield(worker_db.close())
