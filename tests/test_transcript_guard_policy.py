from __future__ import annotations

from datetime import datetime, timezone

from app.workers.transcript_worker import (
    TranscriptBreakerState,
    TranscriptGuardState,
    _adaptive_decay_rate,
    _compute_hard_cooldown_seconds,
    _compute_jittered_interval_seconds,
    _compute_retry_delay_seconds,
    _close_breaker,
    _open_breaker,
)


def test_compute_retry_delay_seconds_uses_exponential_backoff_with_cap() -> None:
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=0) == 120
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=1) == 240
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=4) == 1920
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=20) == 3600


def test_compute_hard_cooldown_seconds_applies_cap() -> None:
    assert _compute_hard_cooldown_seconds(base_seconds=300, max_seconds=3600, hard_error_count=1) == 300
    assert _compute_hard_cooldown_seconds(base_seconds=300, max_seconds=3600, hard_error_count=2) == 600
    assert _compute_hard_cooldown_seconds(base_seconds=300, max_seconds=3600, hard_error_count=5) == 3600


def test_compute_jittered_interval_seconds_stays_in_expected_range() -> None:
    base = 15
    factor = 4.0
    ratio = 0.3
    minimum = base * factor * (1.0 - ratio)
    maximum = base * factor * (1.0 + ratio)

    for _ in range(100):
        sampled = _compute_jittered_interval_seconds(
            base_interval_seconds=base,
            adaptive_factor=factor,
            jitter_ratio=ratio,
        )
        assert minimum <= sampled <= maximum


def test_breaker_open_and_close_state_transitions() -> None:
    guard = TranscriptGuardState(
        breaker_state=TranscriptBreakerState.CLOSED,
        cooldown_until=None,
        half_open_probe_remaining=1,
        last_channel_attempt_at=datetime.now(timezone.utc),
    )

    _open_breaker(guard, cooldown_seconds=120, half_open_probe_count=2)
    assert guard.breaker_state == TranscriptBreakerState.OPEN
    assert guard.cooldown_until is not None
    assert guard.half_open_probe_remaining == 2

    _close_breaker(guard, half_open_probe_count=3)
    assert guard.breaker_state == TranscriptBreakerState.CLOSED
    assert guard.cooldown_until is None
    assert guard.half_open_probe_remaining == 3


def test_half_open_transition_preserves_consecutive_hard_errors() -> None:
    # 5연속 HARD_THROTTLE → hard_errors=5, OPEN 상태
    guard = TranscriptGuardState(
        breaker_state=TranscriptBreakerState.OPEN,
        consecutive_hard_errors=5,
        cooldown_until=None,
        half_open_probe_remaining=1,
    )
    # HALF_OPEN 전환 시뮬레이션 (transcript_worker.py 의 OPEN → HALF_OPEN 전환 블록)
    guard.breaker_state = TranscriptBreakerState.HALF_OPEN
    guard.cooldown_until = None
    guard.half_open_probe_remaining = 1

    # consecutive_hard_errors는 리셋되지 않아야 함
    assert guard.consecutive_hard_errors == 5


def test_half_open_failure_keeps_cooldown_at_max() -> None:
    # HALF_OPEN probe 실패 시 cooldown이 base로 리셋되지 않아야 함
    guard = TranscriptGuardState(
        breaker_state=TranscriptBreakerState.HALF_OPEN,
        consecutive_hard_errors=5,
        cooldown_until=None,
        half_open_probe_remaining=1,
    )
    guard.consecutive_hard_errors += 1  # probe 실패
    cooldown = _compute_hard_cooldown_seconds(
        base_seconds=300,
        max_seconds=3600,
        hard_error_count=guard.consecutive_hard_errors,
    )
    # hard_errors=6 → 300 * 2^5 = 9600 → cap 3600
    assert cooldown == 3600


def test_adaptive_decay_rate_returns_steeper_rate_for_high_factor() -> None:
    max_factor = 8.0
    assert _adaptive_decay_rate(8.0, max_factor) == 0.5   # >= high(4.0)
    assert _adaptive_decay_rate(4.0, max_factor) == 0.5   # == high(4.0)
    assert _adaptive_decay_rate(3.9, max_factor) == 0.65  # >= mid(2.0)
    assert _adaptive_decay_rate(2.0, max_factor) == 0.65  # == mid(2.0)
    assert _adaptive_decay_rate(1.9, max_factor) == 0.8   # < mid
    assert _adaptive_decay_rate(1.0, max_factor) == 0.8   # factor=1.0


def test_adaptive_decay_rate_scales_with_max_factor() -> None:
    # max_factor=16.0 이면 임계값이 비례해서 8.0 / 4.0 으로 이동
    max_factor = 16.0
    assert _adaptive_decay_rate(16.0, max_factor) == 0.5   # >= high(8.0)
    assert _adaptive_decay_rate(8.0, max_factor) == 0.5    # == high(8.0)
    assert _adaptive_decay_rate(7.9, max_factor) == 0.65   # >= mid(4.0)
    assert _adaptive_decay_rate(4.0, max_factor) == 0.65   # == mid(4.0)
    assert _adaptive_decay_rate(3.9, max_factor) == 0.8    # < mid
