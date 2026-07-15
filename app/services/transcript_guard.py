from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

import aiosqlite

# The library exposes these typed exception classes from a private module.
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

from app.repositories import transcripts as transcripts_repo


class TranscriptGuardRuntime(Protocol):
    """Runtime capability required to serialize transcript guard mutations."""

    transcript_guard_lock: asyncio.Lock


class TranscriptErrorCategory(StrEnum):
    NO_SUBTITLE = "no_subtitle"
    HARD_THROTTLE = "hard_throttle"
    RETRYABLE_TRANSIENT = "retryable_transient"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"


class TranscriptBreakerState(StrEnum):
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


def _compute_hard_cooldown_seconds(
    base_seconds: int, max_seconds: int, hard_error_count: int
) -> int:
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
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
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
    def from_repository(cls, payload: dict[str, Any]) -> TranscriptGuardState:
        state_raw = (
            str(payload.get("breaker_state") or TranscriptBreakerState.CLOSED.value).strip().lower()
        )
        if state_raw not in {item.value for item in TranscriptBreakerState}:
            state_raw = TranscriptBreakerState.CLOSED.value
        return cls(
            adaptive_factor=max(1.0, float(payload.get("adaptive_factor") or 1.0)),
            cooldown_until=_parse_cooldown(payload.get("cooldown_until")),
            consecutive_hard_errors=max(0, int(payload.get("consecutive_hard_errors") or 0)),
            consecutive_successes=max(0, int(payload.get("consecutive_successes") or 0)),
            breaker_state=TranscriptBreakerState(state_raw),
            half_open_probe_remaining=max(
                0,
                int(
                    payload["half_open_probe_remaining"]
                    if payload.get("half_open_probe_remaining") is not None
                    else 1
                ),
            ),
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


@asynccontextmanager
async def transcript_guard_mutation(
    state: TranscriptGuardRuntime, db: aiosqlite.Connection
) -> AsyncIterator[TranscriptGuardState]:
    """``TranscriptGuardState``를 ``state.transcript_guard_lock`` 안에서
    ``DB → in-memory mutate → DB`` 순으로 다룬다.

    background transcript, manual transcript, manual article 워커가 같은
    lock을 공유하므로 동시 가드 mutation이 lost update로 깨지지 않는다.
    lock 안에서는 항상 DB의 최신 가드를 다시 읽기 때문에 다른 워커의
    mutation 결과를 즉시 반영한다.

    영상 상태 write(``save_transcript``, ``schedule_transcript_retry``,
    ``defer_channel_transcript_retries`` 등)는 guard write와 같은
    트랜잭션에 묶지 않는다. 공유 ``state.db`` 연결에서 두 종류의 write를
    한 commit으로 합치면 다른 워커 write가 끼어들 여지가 생긴다.
    ``state.transcript_guard_lock`` 필드가 없으면(테스트 등) lock 없이
    동작한다.
    """
    lock = getattr(state, "transcript_guard_lock", None)
    cm = lock if lock is not None else contextlib.nullcontext()
    async with cm:
        persisted = await transcripts_repo.get_transcript_guard_state(db)
        guard = TranscriptGuardState.from_repository(persisted)
        try:
            yield guard
        finally:
            await _save_guard_state(db, guard)


async def read_transcript_guard(
    state: TranscriptGuardRuntime, db: aiosqlite.Connection
) -> TranscriptGuardState:
    """``state.transcript_guard_lock`` 안에서 DB의 최신 가드를 읽어 반환한다
    (저장 없음). 사이클 상단의 분기 결정에 사용한다.

    ``half_open_probe_remaining``은 half-open 상태에서 probe를 모두 소진했음을
    나타내기 위해 0 저장/로드를 허용한다.
    """
    lock = getattr(state, "transcript_guard_lock", None)
    cm = lock if lock is not None else contextlib.nullcontext()
    async with cm:
        persisted = await transcripts_repo.get_transcript_guard_state(db)
        return TranscriptGuardState.from_repository(persisted)


async def claim_transcript_fetch_permit(
    state: TranscriptGuardRuntime,
    db: aiosqlite.Connection,
    *,
    channel_id: str,
    half_open_probe_count: int,
) -> tuple[bool, TranscriptGuardState]:
    """최신 guard 상태로 transcript fetch 허용 여부를 판정하고 저장한다.

    외부 fetch 직전에 호출해야 한다. lock 안에서 최신 상태를 다시 읽은 뒤,
    ``OPEN`` cooldown이면 fetch를 거부하고, ``HALF_OPEN``이면 probe 잔여분이
    있을 때만 하나를 소비한다. 허용된 경우 마지막 채널 시도 시각도 같은
    save로 기록한다.
    """
    lock = getattr(state, "transcript_guard_lock", None)
    cm = lock if lock is not None else contextlib.nullcontext()
    async with cm:
        persisted = await transcripts_repo.get_transcript_guard_state(db)
        guard = TranscriptGuardState.from_repository(persisted)
        now = datetime.now(UTC)
        if guard.breaker_state == TranscriptBreakerState.OPEN:
            if guard.cooldown_until and now < guard.cooldown_until:
                return False, guard
            mark_transcript_guard_half_open(
                guard,
                half_open_probe_count=half_open_probe_count,
            )
        if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
            if guard.half_open_probe_remaining <= 0:
                return False, guard
            guard.half_open_probe_remaining -= 1
        guard.last_channel_id = channel_id or guard.last_channel_id
        guard.last_channel_attempt_at = now
        await _save_guard_state(db, guard)
        return True, guard


def _open_breaker(
    guard: TranscriptGuardState,
    *,
    cooldown_seconds: int,
    half_open_probe_count: int,
) -> None:
    guard.breaker_state = TranscriptBreakerState.OPEN
    guard.cooldown_until = datetime.now(UTC) + timedelta(seconds=max(1, int(cooldown_seconds)))
    guard.half_open_probe_remaining = max(1, int(half_open_probe_count))


def _close_breaker(guard: TranscriptGuardState, *, half_open_probe_count: int) -> None:
    guard.breaker_state = TranscriptBreakerState.CLOSED
    guard.cooldown_until = None
    guard.half_open_probe_remaining = max(1, int(half_open_probe_count))


def record_transcript_guard_success(
    guard: TranscriptGuardState,
    *,
    adaptive_enabled: bool,
    recovery_success_window: int,
    adaptive_max_factor: float,
    half_open_probe_count: int,
) -> None:
    guard.consecutive_successes += 1
    guard.consecutive_hard_errors = 0
    if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
        _close_breaker(guard, half_open_probe_count=half_open_probe_count)
    if adaptive_enabled and guard.consecutive_successes >= recovery_success_window:
        guard.consecutive_successes = 0
        decay = _adaptive_decay_rate(guard.adaptive_factor, adaptive_max_factor)
        guard.adaptive_factor = max(1.0, guard.adaptive_factor * decay)


def mark_transcript_guard_half_open(
    guard: TranscriptGuardState,
    *,
    half_open_probe_count: int,
) -> None:
    guard.breaker_state = TranscriptBreakerState.HALF_OPEN
    guard.cooldown_until = None
    guard.half_open_probe_remaining = max(1, int(half_open_probe_count))


def record_transcript_guard_hard_throttle(
    guard: TranscriptGuardState,
    *,
    adaptive_max_factor: float,
    hard_cooldown_base_seconds: int,
    hard_cooldown_max_seconds: int,
    half_open_probe_count: int,
) -> int:
    guard.consecutive_hard_errors += 1
    guard.consecutive_successes = 0
    guard.adaptive_factor = min(adaptive_max_factor, guard.adaptive_factor * 2.0)
    cooldown_seconds = _compute_hard_cooldown_seconds(
        hard_cooldown_base_seconds,
        hard_cooldown_max_seconds,
        guard.consecutive_hard_errors,
    )
    _open_breaker(
        guard,
        cooldown_seconds=cooldown_seconds,
        half_open_probe_count=half_open_probe_count,
    )
    return cooldown_seconds


def record_transcript_guard_half_open_retryable_failure(
    guard: TranscriptGuardState,
    *,
    adaptive_enabled: bool,
    adaptive_max_factor: float,
    general_error_slowdown_multiplier: float,
    hard_cooldown_base_seconds: int,
    hard_cooldown_max_seconds: int,
    half_open_probe_count: int,
) -> int:
    guard.consecutive_successes = 0
    if adaptive_enabled:
        guard.adaptive_factor = min(
            adaptive_max_factor,
            guard.adaptive_factor * general_error_slowdown_multiplier,
        )
    return _reopen_breaker_after_half_open_retryable_failure(
        guard,
        hard_cooldown_base_seconds=hard_cooldown_base_seconds,
        hard_cooldown_max_seconds=hard_cooldown_max_seconds,
        half_open_probe_count=half_open_probe_count,
    )


def record_transcript_guard_general_failure(
    guard: TranscriptGuardState,
    *,
    adaptive_enabled: bool,
    adaptive_max_factor: float,
    general_error_slowdown_multiplier: float,
    half_open_probe_count: int,
) -> None:
    guard.consecutive_successes = 0
    guard.consecutive_hard_errors = 0
    if guard.breaker_state == TranscriptBreakerState.HALF_OPEN:
        _close_breaker(guard, half_open_probe_count=half_open_probe_count)
    if adaptive_enabled:
        guard.adaptive_factor = min(
            adaptive_max_factor,
            guard.adaptive_factor * general_error_slowdown_multiplier,
        )


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
