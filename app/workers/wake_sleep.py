from __future__ import annotations

import asyncio
import time


async def sleep_with_wake_event(
    state: object,
    event_attr: str,
    timeout_seconds: float,
    *,
    min_timeout_seconds: float = 0.0,
) -> None:
    safe_timeout = max(float(min_timeout_seconds), float(timeout_seconds))
    wake_event = getattr(state, event_attr, None)
    if not isinstance(wake_event, asyncio.Event):
        await asyncio.sleep(safe_timeout)
        return
    if wake_event.is_set():
        wake_event.clear()
        return
    try:
        await asyncio.wait_for(wake_event.wait(), timeout=safe_timeout)
    except TimeoutError:
        return
    finally:
        if wake_event.is_set():
            wake_event.clear()


async def wait_until_monotonic(monotonic_deadline: float) -> None:
    while True:
        remaining = monotonic_deadline - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 0.5))


def runtime_recover_policy(
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
