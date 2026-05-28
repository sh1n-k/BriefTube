from __future__ import annotations

import asyncio
import logging

import httpx

from app.repositories import alerts_retention as alerts_repo
from app.repositories import settings as settings_repo
from app.state import AppState, invalidate_alert_groups_cache

logger = logging.getLogger(__name__)

NOTIFIER_SEND_RETRY_LIMIT = 3
NOTIFIER_BACKOFF_BASE_SECONDS = 2.0
NOTIFIER_BACKOFF_MAX_SECONDS = 60.0


def _format_batch_message(batch: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for item in batch:
        lines.extend(
            [
                f"📰 <b>{item['title']}</b>",
                "---",
                item["lead"],
                f"🔗 https://youtube.com/watch?v={item['video_id']}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def _extract_telegram_retry_after(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None
    parameters = payload.get("parameters")
    if isinstance(parameters, dict):
        candidate = parameters.get("retry_after")
        try:
            if candidate is not None:
                return max(0.0, float(candidate))
        except (TypeError, ValueError):
            pass
    candidate = payload.get("retry_after")
    try:
        if candidate is not None:
            return max(0.0, float(candidate))
    except (TypeError, ValueError):
        pass
    return None


def _classify_http_error(exc: httpx.HTTPStatusError) -> tuple[bool, float | None]:
    response = exc.response
    status = response.status_code if response is not None else None
    retry_after: float | None = None
    if response is not None:
        try:
            retry_after = _extract_telegram_retry_after(response.json())
        except Exception:
            retry_after = None
        if retry_after is None:
            header = response.headers.get("Retry-After") if response.headers else None
            try:
                if header is not None:
                    retry_after = max(0.0, float(header))
            except (TypeError, ValueError):
                retry_after = None
    transient = status == 429 or (status is not None and 500 <= status < 600)
    return transient, retry_after


def _capped_backoff_seconds(attempt: int) -> float:
    return min(
        NOTIFIER_BACKOFF_MAX_SECONDS,
        NOTIFIER_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
    )


async def _send_with_retry(
    state: AppState,
    message: str,
    batch_size: int,
) -> tuple[bool, str]:
    attempt = 0
    last_reason = ""
    while attempt < NOTIFIER_SEND_RETRY_LIMIT:
        attempt += 1
        try:
            response = await state.telegram_notifier.send(message)
        except httpx.HTTPStatusError as exc:
            transient, retry_after = _classify_http_error(exc)
            status = exc.response.status_code if exc.response is not None else None
            last_reason = f"http_{status or 'unknown'}"
            if not transient or attempt >= NOTIFIER_SEND_RETRY_LIMIT:
                logger.warning(
                    "event=notifier.send_http_error worker=notifier batch_size=%s status=%s attempt=%s transient=%s",
                    batch_size,
                    status,
                    attempt,
                    transient,
                    extra={
                        "event": "notifier.send_http_error",
                        "worker": "notifier",
                        "code": str(status or "-"),
                    },
                )
                return False, last_reason
            delay = retry_after if retry_after is not None else _capped_backoff_seconds(attempt)
            logger.info(
                "event=notifier.send_retry worker=notifier batch_size=%s status=%s attempt=%s sleep=%.1fs",
                batch_size,
                status,
                attempt,
                delay,
                extra={
                    "event": "notifier.send_retry",
                    "worker": "notifier",
                    "code": str(status or "-"),
                },
            )
            await asyncio.sleep(min(NOTIFIER_BACKOFF_MAX_SECONDS, delay))
            continue
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_reason = exc.__class__.__name__
            if attempt >= NOTIFIER_SEND_RETRY_LIMIT:
                logger.warning(
                    "event=notifier.send_network_error worker=notifier batch_size=%s error=%s attempt=%s",
                    batch_size,
                    last_reason,
                    attempt,
                    extra={
                        "event": "notifier.send_network_error",
                        "worker": "notifier",
                        "code": last_reason,
                    },
                )
                return False, last_reason
            delay = _capped_backoff_seconds(attempt)
            logger.info(
                "event=notifier.send_retry worker=notifier batch_size=%s error=%s attempt=%s sleep=%.1fs",
                batch_size,
                last_reason,
                attempt,
                delay,
                extra={"event": "notifier.send_retry", "worker": "notifier", "code": last_reason},
            )
            await asyncio.sleep(min(NOTIFIER_BACKOFF_MAX_SECONDS, delay))
            continue
        except Exception as exc:
            last_reason = f"unhandled_{exc.__class__.__name__}"
            logger.exception(
                "event=notifier.send_unhandled_error worker=notifier batch_size=%s attempt=%s",
                batch_size,
                attempt,
                extra={
                    "event": "notifier.send_unhandled_error",
                    "worker": "notifier",
                    "code": exc.__class__.__name__,
                },
            )
            return False, last_reason

        if not isinstance(response, dict):
            last_reason = "non_dict_response"
            logger.warning(
                "event=notifier.send_invalid_response worker=notifier batch_size=%s attempt=%s response_type=%s",
                batch_size,
                attempt,
                type(response).__name__,
                extra={"event": "notifier.send_invalid_response", "worker": "notifier"},
            )
            return False, last_reason

        if response.get("ok", False):
            return True, ""
        retry_after = _extract_telegram_retry_after(response)
        last_reason = str(response.get("description") or "ok=false")[:200]
        if attempt >= NOTIFIER_SEND_RETRY_LIMIT or retry_after is None:
            logger.warning(
                "event=notifier.send_failed worker=notifier batch_size=%s attempt=%s response=%s",
                batch_size,
                attempt,
                response,
                extra={"event": "notifier.send_failed", "worker": "notifier"},
            )
            return False, last_reason
        logger.info(
            "event=notifier.send_retry worker=notifier batch_size=%s attempt=%s sleep=%.1fs reason=%s",
            batch_size,
            attempt,
            retry_after,
            last_reason,
            extra={"event": "notifier.send_retry", "worker": "notifier"},
        )
        await asyncio.sleep(min(NOTIFIER_BACKOFF_MAX_SECONDS, retry_after))
    return False, last_reason


async def run_telegram_notifier(state: AppState) -> None:
    logger.info(
        "event=notifier.worker_started worker=notifier",
        extra={"event": "notifier.worker_started", "worker": "notifier"},
    )
    while True:
        if not state.telegram_notifier.is_configured():
            await asyncio.sleep(5)
            continue
        if not await settings_repo.is_worker_enabled(state.db, "notifier"):
            await asyncio.sleep(3)
            continue

        try:
            try:
                item = await asyncio.wait_for(state.notification_queue.get(), timeout=3)
            except TimeoutError:
                continue
            batch = [item]

            await asyncio.sleep(1)
            while True:
                try:
                    batch.append(state.notification_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            message = _format_batch_message(batch)
            ok, reason = await _send_with_retry(state, message, len(batch))
            if ok:
                logger.info(
                    "event=notifier.send_succeeded worker=notifier batch_size=%s",
                    len(batch),
                    extra={"event": "notifier.send_succeeded", "worker": "notifier"},
                )
            else:
                try:
                    await alerts_repo.create_system_alert(
                        state.db,
                        alert_type=alerts_repo.ALERT_TYPE_TELEGRAM_SEND_FAILED,
                        message=(
                            f"Telegram send failed after {NOTIFIER_SEND_RETRY_LIMIT} attempts "
                            f"(batch_size={len(batch)}): {reason}"
                        )[:500],
                    )
                    invalidate_alert_groups_cache(state)
                except Exception:
                    logger.exception(
                        "event=notifier.alert_record_failed worker=notifier",
                        extra={"event": "notifier.alert_record_failed", "worker": "notifier"},
                    )
                logger.warning(
                    "event=notifier.batch_dropped worker=notifier batch_size=%s reason=%s",
                    len(batch),
                    reason,
                    extra={
                        "event": "notifier.batch_dropped",
                        "worker": "notifier",
                        "code": reason or "-",
                    },
                )
        except Exception:
            logger.exception(
                "event=notifier.worker_loop_failed worker=notifier",
                extra={"event": "notifier.worker_loop_failed", "worker": "notifier"},
            )
            await asyncio.sleep(3)
