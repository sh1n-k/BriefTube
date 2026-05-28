from __future__ import annotations

import asyncio
import logging
import random
import time

from app.repositories import channels as channels_repo
from app.state import AppState
from app.workers.wake_sleep import sleep_with_wake_event

logger = logging.getLogger(__name__)

REQUEST_INTERVAL_MIN_SECONDS = 15.0
REQUEST_INTERVAL_MAX_SECONDS = 30.0
IDLE_WAIT_SECONDS = 60.0
RATE_LIMIT_COOLDOWN_BASE_SECONDS = 300.0
RATE_LIMIT_COOLDOWN_MAX_SECONDS = 1800.0


async def _sleep_with_wake(state: AppState, timeout_seconds: float) -> None:
    await sleep_with_wake_event(
        state,
        "channel_metadata_wake_event",
        timeout_seconds,
        min_timeout_seconds=0.1,
    )


async def run_channel_metadata_worker(state: AppState) -> None:
    logger.info(
        "event=channels.meta.worker_started min_interval_sec=%s max_interval_sec=%s",
        REQUEST_INTERVAL_MIN_SECONDS,
        REQUEST_INTERVAL_MAX_SECONDS,
        extra={"event": "channels.meta.worker_started"},
    )
    next_allowed_at = 0.0
    rate_limit_cooldown_until = 0.0
    consecutive_rate_limited = 0
    while True:
        try:
            now = time.monotonic()
            if now < rate_limit_cooldown_until:
                await _sleep_with_wake(state, rate_limit_cooldown_until - now)
                continue

            job = await channels_repo.claim_next_channel_metadata_target(state.db)
            if job is None:
                await _sleep_with_wake(state, IDLE_WAIT_SECONDS)
                continue

            channel_id = str(job.get("channel_id") or "").strip()
            if not channel_id:
                await _sleep_with_wake(state, 0.5)
                continue

            now = time.monotonic()
            if now < next_allowed_at:
                await _sleep_with_wake(state, next_allowed_at - now)

            logger.info(
                "event=channels.meta.fetch_started channel_id=%s",
                channel_id,
                extra={"event": "channels.meta.fetch_started"},
            )
            result = await state.channel_resolver.fetch_channel_metadata(channel_id)
            request_gap = random.uniform(
                REQUEST_INTERVAL_MIN_SECONDS,
                REQUEST_INTERVAL_MAX_SECONDS,
            )
            next_allowed_at = time.monotonic() + request_gap

            if result.ok:
                await channels_repo.mark_channel_metadata_succeeded(
                    state.db,
                    channel_id=channel_id,
                    channel_name=result.channel_name,
                    channel_handle=result.channel_handle,
                    channel_url_canonical=result.channel_url_canonical,
                    channel_thumbnail_url=result.channel_thumbnail_url,
                    channel_description=result.channel_description,
                    channel_language_hint=result.channel_language_hint,
                    http_status=result.http_status,
                )
                logger.info(
                    "event=channels.meta.fetch_succeeded channel_id=%s handle=%s",
                    channel_id,
                    result.channel_handle or "",
                    extra={"event": "channels.meta.fetch_succeeded"},
                )
                consecutive_rate_limited = 0
                rate_limit_cooldown_until = 0.0
                continue

            await channels_repo.mark_channel_metadata_failed(
                state.db,
                channel_id=channel_id,
                error_message=result.error or "metadata_fetch_failed",
                http_status=result.http_status,
                is_rate_limited=result.is_rate_limited,
            )
            if result.is_rate_limited:
                consecutive_rate_limited = min(5, consecutive_rate_limited + 1)
                cooldown = min(
                    RATE_LIMIT_COOLDOWN_MAX_SECONDS,
                    RATE_LIMIT_COOLDOWN_BASE_SECONDS * (2 ** (consecutive_rate_limited - 1)),
                )
                rate_limit_cooldown_until = time.monotonic() + cooldown
                logger.warning(
                    "event=channels.meta.fetch_rate_limited channel_id=%s http_status=%s cooldown_sec=%.1f",
                    channel_id,
                    result.http_status or 0,
                    cooldown,
                    extra={"event": "channels.meta.fetch_rate_limited"},
                )
            else:
                consecutive_rate_limited = 0
                rate_limit_cooldown_until = 0.0
                logger.warning(
                    "event=channels.meta.fetch_failed channel_id=%s error=%s http_status=%s",
                    channel_id,
                    result.error or "unknown",
                    result.http_status or 0,
                    extra={"event": "channels.meta.fetch_failed"},
                )
        except asyncio.CancelledError:
            logger.info(
                "event=channels.meta.worker_stopped",
                extra={"event": "channels.meta.worker_stopped"},
            )
            raise
        except Exception:
            logger.exception(
                "event=channels.meta.worker_loop_failed",
                extra={"event": "channels.meta.worker_loop_failed"},
            )
            await _sleep_with_wake(state, 3.0)
