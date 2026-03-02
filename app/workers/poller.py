from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import random

import httpx

from app.repositories import alerts_retention as alerts_repo
from app.repositories import channels as channels_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.repository import is_newer_published
from app.state import AppState

logger = logging.getLogger(__name__)


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


async def run_rss_poller(state: AppState) -> None:
    interval_seconds = max(60, state.config.polling_interval_minutes * 60)
    base_delay = max(0.0, state.config.rss_inter_channel_delay_seconds)
    check_step_seconds = 5
    manual_trigger = False

    while True:
        if not await settings_repo.is_worker_enabled(state.db, "rss"):
            if state.poll_now_event.is_set():
                state.poll_now_event.clear()
            await asyncio.sleep(5)
            continue

        delay = base_delay * 0.25 if manual_trigger else base_delay

        try:
            inserted = await poll_once(state, inter_channel_delay=delay)
            if inserted:
                logger.info(
                    "event=rss.poll_completed worker=rss inserted=%s",
                    inserted,
                    extra={"event": "rss.poll_completed", "worker": "rss"},
                )
        except Exception:
            logger.exception(
                "event=rss.poll_failed worker=rss manual_trigger=%s",
                manual_trigger,
                extra={"event": "rss.poll_failed", "worker": "rss"},
            )
        else:
            manual_trigger = False

        try:
            remaining = interval_seconds
            while remaining > 0:
                if not await settings_repo.is_worker_enabled(state.db, "rss"):
                    if state.poll_now_event.is_set():
                        state.poll_now_event.clear()
                    break
                step = min(check_step_seconds, remaining)
                try:
                    await asyncio.wait_for(state.poll_now_event.wait(), timeout=step)
                    state.poll_now_event.clear()
                    manual_trigger = True
                    logger.debug(
                        "event=rss.manual_trigger_consumed worker=rss",
                        extra={"event": "rss.manual_trigger_consumed", "worker": "rss"},
                    )
                    break
                except asyncio.TimeoutError:
                    remaining -= step
        except Exception:
            logger.exception(
                "event=rss.wait_loop_failed worker=rss",
                extra={"event": "rss.wait_loop_failed", "worker": "rss"},
            )
            await asyncio.sleep(10)


async def poll_once(state: AppState, *, inter_channel_delay: float = 0.0) -> int:
    channels = await channels_repo.list_active_channels(state.db)
    policy = await settings_repo.get_policy_settings(state.db)
    lookback_days = max(1, int(policy["rss_bootstrap_lookback_days"]))
    feed_mode = str(policy.get("rss_feed_mode", "long_form_only"))
    started_at = getattr(state, "started_at", datetime.now(timezone.utc))
    lower_bound = started_at - timedelta(days=lookback_days)
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
        channel_id = channel["channel_id"]
        channel_name = channel.get("channel_name") or channel_id
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
                await channels_repo.deactivate_channel(state.db, channel_id)
                await alerts_repo.create_system_alert(
                    state.db,
                    alert_type=alerts_repo.ALERT_TYPE_RSS_CHANNEL_NOT_FOUND,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    message="RSS feed returned 404 Not Found. Channel was deactivated automatically.",
                )
                state.rss_cache.pop(channel_id, None)
                logger.warning(
                    "event=rss.feed_missing worker=rss channel_id=%s channel_name=%s action=deactivate",
                    channel_id,
                    channel_name,
                    extra={
                        "event": "rss.feed_missing",
                        "worker": "rss",
                        "category": "rss_channel_not_found",
                        "code": "404",
                    },
                )
                continue
            logger.warning(
                "event=rss.fetch_failed worker=rss channel_id=%s status=%s",
                channel_id,
                status_code,
                extra={"event": "rss.fetch_failed", "worker": "rss", "code": str(status_code or "-")},
            )
            continue
        except Exception:
            logger.exception(
                "event=rss.fetch_exception worker=rss channel_id=%s",
                channel_id,
                extra={"event": "rss.fetch_exception", "worker": "rss"},
            )
            continue

        state.rss_cache[channel_id] = {
            "etag": new_etag or "",
            "last_modified": new_last_modified or "",
            "feed_mode": feed_mode,
        }

        watermark = channel.get("last_seen_published_at")
        max_published = watermark

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
                total_inserted += 1

            if max_published is None or is_newer_published(published, max_published):
                max_published = published

        if max_published and max_published != watermark:
            await channels_repo.update_channel_watermark(state.db, channel_id, max_published)

    return total_inserted
