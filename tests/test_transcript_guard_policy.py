from __future__ import annotations

from datetime import UTC, datetime

from app.services.transcript_guard import (
    TranscriptBreakerState,
    TranscriptGuardState,
    _adaptive_decay_rate,
    _close_breaker,
    _compute_hard_cooldown_seconds,
    _compute_jittered_interval_seconds,
    _compute_retry_delay_seconds,
    _open_breaker,
    _reopen_breaker_after_half_open_retryable_failure,
    mark_transcript_guard_half_open,
    record_transcript_guard_general_failure,
    record_transcript_guard_half_open_retryable_failure,
    record_transcript_guard_hard_throttle,
    record_transcript_guard_success,
)


def test_compute_retry_delay_seconds_uses_exponential_backoff_with_cap() -> None:
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=0) == 120
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=1) == 240
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=4) == 1920
    assert _compute_retry_delay_seconds(base_delay=120, max_delay=3600, retry_count=20) == 3600


def test_compute_hard_cooldown_seconds_applies_cap() -> None:
    assert (
        _compute_hard_cooldown_seconds(base_seconds=300, max_seconds=3600, hard_error_count=1)
        == 300
    )
    assert (
        _compute_hard_cooldown_seconds(base_seconds=300, max_seconds=3600, hard_error_count=2)
        == 600
    )
    assert (
        _compute_hard_cooldown_seconds(base_seconds=300, max_seconds=3600, hard_error_count=5)
        == 3600
    )


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
        last_channel_attempt_at=datetime.now(UTC),
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
    mark_transcript_guard_half_open(guard, half_open_probe_count=1)

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


def test_reopen_breaker_after_half_open_retryable_failure_preserves_hard_error_level() -> None:
    guard = TranscriptGuardState(
        breaker_state=TranscriptBreakerState.HALF_OPEN,
        consecutive_hard_errors=5,
        cooldown_until=None,
        half_open_probe_remaining=1,
    )

    cooldown = _reopen_breaker_after_half_open_retryable_failure(
        guard,
        hard_cooldown_base_seconds=300,
        hard_cooldown_max_seconds=3600,
        half_open_probe_count=2,
    )

    assert cooldown == 3600
    assert guard.breaker_state == TranscriptBreakerState.OPEN
    assert guard.cooldown_until is not None
    assert guard.half_open_probe_remaining == 2
    assert guard.consecutive_hard_errors == 5


def test_adaptive_decay_rate_returns_steeper_rate_for_high_factor() -> None:
    max_factor = 8.0
    assert _adaptive_decay_rate(8.0, max_factor) == 0.5  # >= high(4.0)
    assert _adaptive_decay_rate(4.0, max_factor) == 0.5  # == high(4.0)
    assert _adaptive_decay_rate(3.9, max_factor) == 0.65  # >= mid(2.0)
    assert _adaptive_decay_rate(2.0, max_factor) == 0.65  # == mid(2.0)
    assert _adaptive_decay_rate(1.9, max_factor) == 0.8  # < mid
    assert _adaptive_decay_rate(1.0, max_factor) == 0.8  # factor=1.0


def test_adaptive_decay_rate_scales_with_max_factor() -> None:
    # max_factor=16.0 이면 임계값이 비례해서 8.0 / 4.0 으로 이동
    max_factor = 16.0
    assert _adaptive_decay_rate(16.0, max_factor) == 0.5  # >= high(8.0)
    assert _adaptive_decay_rate(8.0, max_factor) == 0.5  # == high(8.0)
    assert _adaptive_decay_rate(7.9, max_factor) == 0.65  # >= mid(4.0)
    assert _adaptive_decay_rate(4.0, max_factor) == 0.65  # == mid(4.0)
    assert _adaptive_decay_rate(3.9, max_factor) == 0.8  # < mid


def test_record_transcript_guard_success_closes_half_open_and_decays_factor() -> None:
    guard = TranscriptGuardState(
        adaptive_factor=4.0,
        consecutive_successes=1,
        consecutive_hard_errors=3,
        breaker_state=TranscriptBreakerState.HALF_OPEN,
        half_open_probe_remaining=0,
    )

    record_transcript_guard_success(
        guard,
        adaptive_enabled=True,
        recovery_success_window=2,
        adaptive_max_factor=8.0,
        half_open_probe_count=3,
    )

    assert guard.breaker_state == TranscriptBreakerState.CLOSED
    assert guard.cooldown_until is None
    assert guard.half_open_probe_remaining == 3
    assert guard.consecutive_successes == 0
    assert guard.consecutive_hard_errors == 0
    assert guard.adaptive_factor == 2.0


def test_record_transcript_guard_hard_throttle_opens_breaker_and_increases_factor() -> None:
    guard = TranscriptGuardState(
        adaptive_factor=3.0,
        consecutive_hard_errors=1,
        consecutive_successes=2,
        breaker_state=TranscriptBreakerState.CLOSED,
    )

    cooldown = record_transcript_guard_hard_throttle(
        guard,
        adaptive_max_factor=5.0,
        hard_cooldown_base_seconds=300,
        hard_cooldown_max_seconds=3600,
        half_open_probe_count=2,
    )

    assert cooldown == 600
    assert guard.breaker_state == TranscriptBreakerState.OPEN
    assert guard.cooldown_until is not None
    assert guard.half_open_probe_remaining == 2
    assert guard.consecutive_hard_errors == 2
    assert guard.consecutive_successes == 0
    assert guard.adaptive_factor == 5.0


def test_record_transcript_guard_half_open_retryable_failure_reopens_with_slowdown() -> None:
    guard = TranscriptGuardState(
        adaptive_factor=2.0,
        consecutive_hard_errors=4,
        consecutive_successes=3,
        breaker_state=TranscriptBreakerState.HALF_OPEN,
        half_open_probe_remaining=1,
    )

    cooldown = record_transcript_guard_half_open_retryable_failure(
        guard,
        adaptive_enabled=True,
        adaptive_max_factor=10.0,
        general_error_slowdown_multiplier=1.5,
        hard_cooldown_base_seconds=300,
        hard_cooldown_max_seconds=3600,
        half_open_probe_count=3,
    )

    assert cooldown == 2400
    assert guard.breaker_state == TranscriptBreakerState.OPEN
    assert guard.cooldown_until is not None
    assert guard.half_open_probe_remaining == 3
    assert guard.consecutive_hard_errors == 4
    assert guard.consecutive_successes == 0
    assert guard.adaptive_factor == 3.0


def test_record_transcript_guard_general_failure_closes_half_open_and_slows_down() -> None:
    guard = TranscriptGuardState(
        adaptive_factor=4.0,
        consecutive_hard_errors=3,
        consecutive_successes=2,
        breaker_state=TranscriptBreakerState.HALF_OPEN,
        half_open_probe_remaining=0,
    )

    record_transcript_guard_general_failure(
        guard,
        adaptive_enabled=True,
        adaptive_max_factor=5.0,
        general_error_slowdown_multiplier=1.5,
        half_open_probe_count=2,
    )

    assert guard.breaker_state == TranscriptBreakerState.CLOSED
    assert guard.cooldown_until is None
    assert guard.half_open_probe_remaining == 2
    assert guard.consecutive_hard_errors == 0
    assert guard.consecutive_successes == 0
    assert guard.adaptive_factor == 5.0


def test_record_transcript_guard_general_failure_keeps_factor_when_adaptive_disabled() -> None:
    guard = TranscriptGuardState(
        adaptive_factor=3.0,
        consecutive_hard_errors=1,
        consecutive_successes=2,
        breaker_state=TranscriptBreakerState.CLOSED,
    )

    record_transcript_guard_general_failure(
        guard,
        adaptive_enabled=False,
        adaptive_max_factor=10.0,
        general_error_slowdown_multiplier=2.0,
        half_open_probe_count=3,
    )

    assert guard.breaker_state == TranscriptBreakerState.CLOSED
    assert guard.consecutive_hard_errors == 0
    assert guard.consecutive_successes == 0
    assert guard.adaptive_factor == 3.0
