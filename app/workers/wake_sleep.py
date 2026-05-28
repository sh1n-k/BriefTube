from __future__ import annotations

import asyncio


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
