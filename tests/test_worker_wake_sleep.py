from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.workers.wake_sleep import sleep_with_wake_event


def test_sleep_with_wake_event_clears_pre_set_event() -> None:
    async def _run() -> bool:
        event = asyncio.Event()
        event.set()
        state = SimpleNamespace(wake_event=event)

        await sleep_with_wake_event(state, "wake_event", 10)

        return event.is_set()

    assert asyncio.run(_run()) is False


def test_sleep_with_wake_event_wakes_and_clears_event() -> None:
    async def _run() -> tuple[bool, float]:
        event = asyncio.Event()
        state = SimpleNamespace(wake_event=event)

        started_at = time.monotonic()
        task = asyncio.create_task(sleep_with_wake_event(state, "wake_event", 10))
        await asyncio.sleep(0)
        event.set()
        await asyncio.wait_for(task, timeout=1)

        return event.is_set(), time.monotonic() - started_at

    is_set, elapsed = asyncio.run(_run())
    assert is_set is False
    assert elapsed < 1


def test_sleep_with_wake_event_uses_min_timeout_without_event() -> None:
    async def _run() -> float:
        state = SimpleNamespace()
        started_at = time.monotonic()

        await sleep_with_wake_event(
            state,
            "missing_event",
            0,
            min_timeout_seconds=0.01,
        )

        return time.monotonic() - started_at

    assert asyncio.run(_run()) >= 0.005


def test_sleep_with_wake_event_preserves_cancellation() -> None:
    async def _run() -> None:
        event = asyncio.Event()
        state = SimpleNamespace(wake_event=event)
        task = asyncio.create_task(sleep_with_wake_event(state, "wake_event", 10))
        await asyncio.sleep(0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())
