from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import random
import time
from typing import Any

import httpx

from app.repositories import alerts_retention as alerts_repo
from app.repositories import channels as channels_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.repositories.channels import is_newer_published
from app.services.rss import RSSParseError
from app.state import AppState

logger = logging.getLogger(__name__)
RSS_404_DEACTIVATE_THRESHOLD = 3
RSS_404_DEACTIVATE_MIN_AGE = timedelta(hours=6)


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


async def run_rss_poller(state: AppState) -> None:
    polling_interval_sec = max(60, state.config.polling_interval_minutes * 60)
    deactivate_threshold = state.config.rss_channel_deactivate_after_fails
    abort_threshold = state.config.rss_consecutive_error_abort_threshold
    jitter_ratio = 0.3
    check_step_seconds = 5

    logger.info(
        "event=rss.poller_started worker=rss interval=%sm deactivate_after=%s abort_after=%s",
        state.config.polling_interval_minutes,
        deactivate_threshold,
        abort_threshold,
        extra={"event": "rss.poller_started", "worker": "rss"},
    )

    while True:
        if not await settings_repo.is_worker_enabled(state.db, "rss"):
            if state.poll_now_event.is_set():
                state.poll_now_event.clear()
            await asyncio.sleep(5)
            continue

        manual_trigger = state.poll_now_event.is_set()
        if manual_trigger:
            state.poll_now_event.clear()

        channel_count = await channels_repo.count_active_channels(state.db)
        if channel_count == 0:
            try:
                await asyncio.wait_for(state.poll_now_event.wait(), timeout=polling_interval_sec)
                state.poll_now_event.clear()
            except asyncio.TimeoutError:
                pass
            continue

        base_delay = polling_interval_sec / channel_count
        if manual_trigger:
            base_delay *= 0.25

        logger.info(
            "event=rss.cycle_started worker=rss channels=%d delay=%.1fs manual=%s",
            channel_count,
            base_delay,
            manual_trigger,
            extra={"event": "rss.cycle_started", "worker": "rss"},
        )

        consecutive_errors = 0
        polled = 0
        inserted_total = 0
        cycle_start = time.monotonic()

        for _ in range(channel_count):
            if not await settings_repo.is_worker_enabled(state.db, "rss"):
                break

            if state.poll_now_event.is_set():
                state.poll_now_event.clear()
                manual_trigger = True
                base_delay = (polling_interval_sec / channel_count) * 0.25

            channel = await channels_repo.pick_next_rss_channel(state.db)
            if channel is None:
                break

            try:
                ok, count = await _poll_single_channel(
                    state,
                    channel=channel,
                    deactivate_threshold=deactivate_threshold,
                )
            except Exception:
                logger.exception(
                    "event=rss.poll_channel_failed worker=rss channel_id=%s",
                    channel.get("channel_id", "-"),
                    extra={"event": "rss.poll_channel_failed", "worker": "rss"},
                )
                ok, count = False, 0

            polled += 1
            inserted_total += count

            if ok:
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                if consecutive_errors >= abort_threshold:
                    logger.info(
                        "event=rss.cycle_aborted worker=rss consecutive_errors=%d threshold=%d polled=%d",
                        consecutive_errors,
                        abort_threshold,
                        polled,
                        extra={"event": "rss.cycle_aborted", "worker": "rss"},
                    )
                    break

            jitter = base_delay * jitter_ratio
            delay = base_delay + random.uniform(-jitter, jitter)
            try:
                await asyncio.wait_for(state.poll_now_event.wait(), timeout=max(0.1, delay))
            except asyncio.TimeoutError:
                pass

        if polled > 0:
            logger.info(
                "event=rss.cycle_completed worker=rss polled=%d inserted=%d",
                polled,
                inserted_total,
                extra={"event": "rss.cycle_completed", "worker": "rss"},
            )

        elapsed = time.monotonic() - cycle_start
        remaining = max(1.0, polling_interval_sec - elapsed)

        try:
            while remaining > 0:
                if not await settings_repo.is_worker_enabled(state.db, "rss"):
                    if state.poll_now_event.is_set():
                        state.poll_now_event.clear()
                    break
                step = min(check_step_seconds, remaining)
                try:
                    await asyncio.wait_for(state.poll_now_event.wait(), timeout=step)
                    break
                except asyncio.TimeoutError:
                    remaining -= step
        except Exception:
            logger.exception(
                "event=rss.wait_loop_failed worker=rss",
                extra={"event": "rss.wait_loop_failed", "worker": "rss"},
            )
            await asyncio.sleep(10)


async def _poll_single_channel(
    state: AppState,
    *,
    channel: dict[str, Any],
    deactivate_threshold: int,
) -> tuple[bool, int]:
    """단일 채널 RSS 폴링. (성공여부, 삽입건수) 반환."""
    channel_id = channel["channel_id"]
    channel_name = channel.get("channel_name") or channel_id

    policy = await settings_repo.get_policy_settings(state.db)
    feed_mode = str(policy.get("rss_feed_mode", "long_form_only"))
    lookback_days = max(1, int(policy["rss_bootstrap_lookback_days"]))
    started_at = getattr(state, "started_at", datetime.now(timezone.utc))
    lower_bound = started_at - timedelta(days=lookback_days)

    cache = state.rss_cache.get(channel_id, {})
    if cache.get("feed_mode", "") != feed_mode:
        etag, last_modified = None, None
    else:
        etag = cache.get("etag")
        last_modified = cache.get("last_modified")

    try:
        entries, new_etag, new_last_modified = await state.rss_service.fetch_channel_feed(
            channel_id=channel_id,
            etag=etag,
            last_modified=last_modified,
            feed_mode=feed_mode,
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code == 404:
            streak = await channels_repo.increment_rss_fail_streak(state.db, channel_id)
            if streak >= deactivate_threshold:
                await channels_repo.deactivate_channel(state.db, channel_id)
                await alerts_repo.create_system_alert(
                    state.db,
                    alert_type=alerts_repo.ALERT_TYPE_RSS_CHANNEL_NOT_FOUND,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    message=f"RSS feed returned 404 Not Found ({streak} consecutive failures). Channel deactivated.",
                )
                state.rss_cache.pop(channel_id, None)
                logger.warning(
                    "event=rss.channel_deactivated worker=rss channel_id=%s channel_name=%s streak=%d threshold=%d",
                    channel_id,
                    channel_name,
                    streak,
                    deactivate_threshold,
                    extra={
                        "event": "rss.channel_deactivated",
                        "worker": "rss",
                        "category": "rss_channel_not_found",
                        "code": "404",
                    },
                )
            else:
                logger.info(
                    "event=rss.feed_not_found worker=rss channel_id=%s streak=%d/%d",
                    channel_id,
                    streak,
                    deactivate_threshold,
                    extra={"event": "rss.feed_not_found", "worker": "rss", "code": "404"},
                )
            return False, 0
        await channels_repo.touch_rss_last_polled_at(state.db, channel_id)
        logger.warning(
            "event=rss.fetch_failed worker=rss channel_id=%s status=%s",
            channel_id,
            status_code,
            extra={"event": "rss.fetch_failed", "worker": "rss", "code": str(status_code or "-")},
        )
        return False, 0
    except RSSParseError:
        await channels_repo.touch_rss_last_polled_at(state.db, channel_id)
        logger.warning(
            "event=rss.parse_failed worker=rss channel_id=%s",
            channel_id,
            extra={"event": "rss.parse_failed", "worker": "rss", "code": "rss_parse_error"},
        )
        return False, 0
    except Exception:
        await channels_repo.touch_rss_last_polled_at(state.db, channel_id)
        logger.exception(
            "event=rss.fetch_exception worker=rss channel_id=%s",
            channel_id,
            extra={"event": "rss.fetch_exception", "worker": "rss"},
        )
        return False, 0

    await channels_repo.mark_rss_poll_success(state.db, channel_id)

    state.rss_cache[channel_id] = {
        "etag": new_etag or "",
        "last_modified": new_last_modified or "",
        "feed_mode": feed_mode,
    }

    watermark = channel.get("last_seen_published_at")
    max_published = watermark
    channel_inserted = 0

    for entry in entries:
        published = entry["published"]
        if watermark is None:
            published_dt = _parse_iso_datetime(published)
            if published_dt and published_dt < lower_bound:
                if max_published is None or is_newer_published(published, max_published):
                    max_published = published
                continue
        if not is_newer_published(published, watermark):
            continue

        inserted = await videos_repo.insert_video_if_absent(
            state.db,
            video_id=entry["video_id"],
            channel_id=channel_id,
            title=entry["title"],
            upload_time=published,
        )
        if inserted:
            channel_inserted += 1

        if max_published is None or is_newer_published(published, max_published):
            max_published = published

    if max_published and max_published != watermark:
        await channels_repo.update_channel_watermark(state.db, channel_id, max_published)

    return True, channel_inserted


async def poll_once(state: AppState, *, inter_channel_delay: float = 0.0) -> int:
    """기존 호환용: 활성 채널 전체를 순회하여 폴링. 삽입 건수 반환."""
    channels = await channels_repo.list_active_channels(state.db)
    config = getattr(state, "config", None)
    deactivate_threshold = getattr(config, "rss_channel_deactivate_after_fails", 3) if config else 3
    total_inserted = 0

    if inter_channel_delay > 0:
        estimated_total = inter_channel_delay * max(0, len(channels) - 1)
        logger.info(
            "event=rss.poll_started worker=rss channels=%d inter_channel_delay=%.1f estimated_total_delay=%.0fs",
            len(channels),
            inter_channel_delay,
            estimated_total,
            extra={"event": "rss.poll_started", "worker": "rss"},
        )

    for i, channel in enumerate(channels):
        if i > 0 and inter_channel_delay > 0:
            jitter = inter_channel_delay * 0.3
            delay = inter_channel_delay + random.uniform(-jitter, jitter)
            await asyncio.sleep(max(0.0, delay))

        ok, count = await _poll_single_channel(
            state,
            channel=channel,
            deactivate_threshold=deactivate_threshold,
        )
        total_inserted += count

    return total_inserted
