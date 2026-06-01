from __future__ import annotations

from app.services.transcript_guard import (
    TranscriptBreakerState,
    TranscriptGuardState,
    _compute_retry_delay_seconds,
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
