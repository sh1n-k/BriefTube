from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.workers.wake_sleep import (
    runtime_recover_policy,
    sleep_with_wake_event,
    wait_until_monotonic,
)


def test_wait_until_monotonic_returns_immediately_when_deadline_passed() -> None:
    async def _run() -> None:
        await wait_until_monotonic(0)

    asyncio.run(_run())


def test_runtime_recover_policy_bounds() -> None:
    stale_after, check_interval = runtime_recover_policy(
        idle_sleep_seconds=10,
        request_interval_seconds=5,
        fetch_timeout_seconds=30,
    )
    assert stale_after == 300
    assert check_interval == 75

    stale_after, check_interval = runtime_recover_policy(
        idle_sleep_seconds=100,
        request_interval_seconds=100,
        fetch_timeout_seconds=100,
    )
    assert stale_after == max(300, 600, 800, 1200)
    assert 30 <= check_interval <= 120


def test_sleep_with_wake_event_clears_pre_set_event() -> None:
    async def _run() -> bool:
        event = asyncio.Event()
        event.set()
        state = SimpleNamespace(wake_event=event)

        await sleep_with_wake_event(state, "wake_event", 10)

        return event.is_set()

    assert asyncio.run(_run()) is False


def test_sleep_with_wake_event_wakes_and_clears_event() -> None:
    async def _run() -> bool:
        event = asyncio.Event()
        state = SimpleNamespace(wake_event=event)

        task = asyncio.create_task(sleep_with_wake_event(state, "wake_event", 10))
        await asyncio.sleep(0)
        event.set()
        await asyncio.wait_for(task, timeout=1)

        return event.is_set()

    assert asyncio.run(_run()) is False


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
