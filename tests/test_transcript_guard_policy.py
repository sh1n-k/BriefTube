from __future__ import annotations

from app.workers.transcript_worker import (
    _compute_hard_cooldown_seconds,
    _compute_jittered_interval_seconds,
    _compute_retry_delay_seconds,
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
