from __future__ import annotations

from datetime import datetime, timezone

from app.workers.transcript_worker import (
    TranscriptBreakerState,
    TranscriptGuardState,
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
