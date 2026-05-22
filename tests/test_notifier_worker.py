from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from app.database import init_database, open_database
from app.workers import notifier_worker


class _FakeNotifier:
    def __init__(self, responses: list, *, raise_exceptions: list | None = None):
        self._responses = list(responses)
        self._exceptions = list(raise_exceptions or [])
        self.calls: list[str] = []

    def is_configured(self) -> bool:
        return True

    async def send(self, message: str) -> dict:
        self.calls.append(message)
        if self._exceptions:
            exc = self._exceptions.pop(0)
            if exc is not None:
                raise exc
        if self._responses:
            return self._responses.pop(0)
        return {"ok": True}


def _build_state(notifier: _FakeNotifier, db, sleep_recorder: list[float]) -> SimpleNamespace:
    queue: asyncio.Queue = asyncio.Queue()
    return SimpleNamespace(
        telegram_notifier=notifier,
        notification_queue=queue,
        db=db,
    )


async def _drive_worker_once(state, monkeypatch) -> None:
    """Run worker until first send result is observed, then cancel."""
    task = asyncio.create_task(notifier_worker.run_telegram_notifier(state))
    try:
        # 워커가 큐를 비울 때까지 대기
        for _ in range(200):
            await asyncio.sleep(0.05)
            if state.telegram_notifier.calls and state.notification_queue.empty():
                # 한 호출 후에도 worker 가 alert/log 작성하도록 약간 더 대기
                await asyncio.sleep(0.2)
                break
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _enqueue_sample(state, count: int = 1) -> None:
    for idx in range(count):
        state.notification_queue.put_nowait(
            {
                "video_id": f"vid-test-{idx:03d}",
                "title": f"Title {idx}",
                "lead": f"Lead {idx}",
            }
        )


def test_send_with_retry_succeeds_on_first_attempt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(notifier_worker, "NOTIFIER_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr(notifier_worker.asyncio, "sleep", lambda *_a, **_kw: asyncio.sleep(0))
    notifier = _FakeNotifier(responses=[{"ok": True}])

    async def _run() -> tuple[bool, str, int]:
        state = SimpleNamespace(telegram_notifier=notifier)
        ok, reason = await notifier_worker._send_with_retry(state, "hello", 1)
        return ok, reason, len(notifier.calls)

    ok, reason, call_count = asyncio.run(_run())
    assert ok is True
    assert reason == ""
    assert call_count == 1


def test_send_with_retry_handles_429_with_retry_after(tmp_path, monkeypatch) -> None:
    sleep_log: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_log.append(float(seconds))

    monkeypatch.setattr(notifier_worker.asyncio, "sleep", _fake_sleep)
    # Telegram-style 429 응답을 ok=False + parameters.retry_after 로 시뮬레이션
    notifier = _FakeNotifier(
        responses=[
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}},
            {"ok": True},
        ]
    )

    async def _run() -> tuple[bool, int]:
        state = SimpleNamespace(telegram_notifier=notifier)
        ok, _ = await notifier_worker._send_with_retry(state, "msg", 2)
        return ok, len(notifier.calls)

    ok, call_count = asyncio.run(_run())
    assert ok is True
    assert call_count == 2
    assert any(abs(s - 7.0) < 0.01 for s in sleep_log), f"retry_after not honored, slept={sleep_log}"


def test_send_with_retry_retries_5xx_then_gives_up(tmp_path, monkeypatch) -> None:
    sleep_log: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_log.append(float(seconds))

    monkeypatch.setattr(notifier_worker.asyncio, "sleep", _fake_sleep)
    request = httpx.Request("POST", "https://api.telegram.org/")
    err = httpx.HTTPStatusError(
        "503",
        request=request,
        response=httpx.Response(503, request=request),
    )
    notifier = _FakeNotifier(responses=[], raise_exceptions=[err, err, err])

    async def _run() -> tuple[bool, str, int]:
        state = SimpleNamespace(telegram_notifier=notifier)
        ok, reason = await notifier_worker._send_with_retry(state, "msg", 1)
        return ok, reason, len(notifier.calls)

    ok, reason, call_count = asyncio.run(_run())
    assert ok is False
    assert reason == "http_503"
    assert call_count == notifier_worker.NOTIFIER_SEND_RETRY_LIMIT


def test_send_with_retry_does_not_retry_4xx_other_than_429(tmp_path, monkeypatch) -> None:
    async def _fake_sleep(seconds: float) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(notifier_worker.asyncio, "sleep", _fake_sleep)
    request = httpx.Request("POST", "https://api.telegram.org/")
    err = httpx.HTTPStatusError(
        "400",
        request=request,
        response=httpx.Response(400, request=request),
    )
    notifier = _FakeNotifier(responses=[], raise_exceptions=[err])

    async def _run() -> tuple[bool, str, int]:
        state = SimpleNamespace(telegram_notifier=notifier)
        ok, reason = await notifier_worker._send_with_retry(state, "msg", 1)
        return ok, reason, len(notifier.calls)

    ok, reason, call_count = asyncio.run(_run())
    assert ok is False
    assert reason == "http_400"
    assert call_count == 1  # no retry


def test_send_with_retry_retries_network_errors(tmp_path, monkeypatch) -> None:
    async def _fake_sleep(seconds: float) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(notifier_worker.asyncio, "sleep", _fake_sleep)
    notifier = _FakeNotifier(
        responses=[{"ok": True}],
        raise_exceptions=[httpx.ConnectTimeout("connect timed out"), None],
    )

    async def _run() -> tuple[bool, int]:
        state = SimpleNamespace(telegram_notifier=notifier)
        ok, _ = await notifier_worker._send_with_retry(state, "msg", 1)
        return ok, len(notifier.calls)

    ok, call_count = asyncio.run(_run())
    assert ok is True
    assert call_count == 2


def test_worker_records_alert_on_final_failure(tmp_path, monkeypatch) -> None:
    """최종 실패 시 system_alerts에 telegram_send_failed 기록되는지 검증."""

    async def _fake_sleep(seconds: float) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(notifier_worker.asyncio, "sleep", _fake_sleep)

    db_path = tmp_path / "notifier-alert.db"
    request = httpx.Request("POST", "https://api.telegram.org/")
    failure = httpx.HTTPStatusError(
        "503",
        request=request,
        response=httpx.Response(503, request=request),
    )

    async def _run() -> int:
        db = await open_database(str(db_path))
        try:
            await init_database(db)

            class _AlwaysFailNotifier(_FakeNotifier):
                pass

            notifier = _AlwaysFailNotifier(
                responses=[],
                raise_exceptions=[failure, failure, failure],
            )

            # notifier 워커가 enabled 라고 응답하도록 settings 키 set
            from app.repositories import settings as settings_repo

            await settings_repo.set_setting(db, "worker_notifier_enabled", "true")
            state = SimpleNamespace(
                telegram_notifier=notifier,
                notification_queue=asyncio.Queue(),
                db=db,
            )
            state.notification_queue.put_nowait(
                {"video_id": "v1", "title": "t1", "lead": "l1"}
            )
            task = asyncio.create_task(notifier_worker.run_telegram_notifier(state))
            try:
                for _ in range(200):
                    await asyncio.sleep(0.02)
                    cursor = await db.execute(
                        "SELECT COUNT(1) FROM system_alerts WHERE alert_type = 'telegram_send_failed'"
                    )
                    row = await cursor.fetchone()
                    if int(row[0] or 0) >= 1:
                        return int(row[0])
                return 0
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            await db.close()

    alert_count = asyncio.run(_run())
    assert alert_count == 1, "telegram_send_failed alert should be recorded once"


def test_send_with_retry_terminal_4xx_via_ok_false(tmp_path, monkeypatch) -> None:
    """Telegram이 200 + ok=false 응답하고 retry_after가 없으면 즉시 종료."""

    async def _fake_sleep(seconds: float) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(notifier_worker.asyncio, "sleep", _fake_sleep)
    notifier = _FakeNotifier(
        responses=[{"ok": False, "description": "Bad Request: chat not found"}]
    )

    async def _run() -> tuple[bool, str, int]:
        state = SimpleNamespace(telegram_notifier=notifier)
        ok, reason = await notifier_worker._send_with_retry(state, "msg", 1)
        return ok, reason, len(notifier.calls)

    ok, reason, call_count = asyncio.run(_run())
    assert ok is False
    assert "Bad Request" in reason
    assert call_count == 1  # no retry without retry_after


def test_send_with_retry_absorbs_unhandled_exception(tmp_path, monkeypatch) -> None:
    """httpx 외의 예외(JSON 파싱 실패 등)도 흡수해서 (False, reason) 반환."""

    async def _fake_sleep(seconds: float) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(notifier_worker.asyncio, "sleep", _fake_sleep)
    notifier = _FakeNotifier(responses=[], raise_exceptions=[RuntimeError("boom")])

    async def _run() -> tuple[bool, str, int]:
        state = SimpleNamespace(telegram_notifier=notifier)
        ok, reason = await notifier_worker._send_with_retry(state, "msg", 1)
        return ok, reason, len(notifier.calls)

    ok, reason, call_count = asyncio.run(_run())
    assert ok is False
    assert reason == "unhandled_RuntimeError"
    assert call_count == 1  # 추가 retry 없이 즉시 종료


def test_send_with_retry_rejects_non_dict_response(tmp_path, monkeypatch) -> None:
    """비-dict 응답은 즉시 종료해 AttributeError를 방지."""

    async def _fake_sleep(seconds: float) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(notifier_worker.asyncio, "sleep", _fake_sleep)
    notifier = _FakeNotifier(responses=[["not", "a", "dict"]])

    async def _run() -> tuple[bool, str, int]:
        state = SimpleNamespace(telegram_notifier=notifier)
        ok, reason = await notifier_worker._send_with_retry(state, "msg", 1)
        return ok, reason, len(notifier.calls)

    ok, reason, call_count = asyncio.run(_run())
    assert ok is False
    assert reason == "non_dict_response"
    assert call_count == 1
