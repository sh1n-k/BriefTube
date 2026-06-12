from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.repositories import alerts_retention as alerts_repo
from app.repositories import channels as channels_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.repositories.channels import is_newer_published
from app.services.rss import RSSParseError
from app.services.yt_dlp_feed import YtDlpFeedError
from app.state import AppState, invalidate_alert_groups_cache, invalidate_retention_notice_cache

logger = logging.getLogger(__name__)

RSS_FETCHER_MODE_RSS = "rss"
RSS_FETCHER_MODE_RSS_THEN_YT_DLP = "rss_then_yt_dlp"
RSS_FETCHER_MODE_YT_DLP = "yt_dlp"
RSS_POLL_MIN_INTERVAL_SECONDS = 300
RSS_POLL_MAX_INTERVAL_SECONDS = 86400
RSS_POLL_PRIORITY_MULTIPLIERS = {
    "pinned": 0.5,
    "normal": 1.0,
    "low": 4.0,
}
RSS_POLL_OUTCOME_MULTIPLIERS = {
    "new": 0.5,
    "unchanged": 2.0,
    "error": 2.0,
    "not_found": 4.0,
}


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def _get_fetcher_mode(state: AppState) -> str:
    config = getattr(state, "config", None)
    mode = str(getattr(config, "rss_fetcher_mode", RSS_FETCHER_MODE_RSS)).strip().lower()
    if mode not in {
        RSS_FETCHER_MODE_RSS,
        RSS_FETCHER_MODE_RSS_THEN_YT_DLP,
        RSS_FETCHER_MODE_YT_DLP,
    }:
        return RSS_FETCHER_MODE_RSS
    return mode


def _get_base_poll_interval_seconds(state: AppState) -> int:
    config = getattr(state, "config", None)
    minutes = getattr(config, "polling_interval_minutes", 15) if config else 15
    return max(60, int(minutes) * 60)


def _rss_interval_seconds(
    state: AppState,
    channel: dict[str, Any],
    outcome: str,
) -> int:
    base_seconds = _get_base_poll_interval_seconds(state)
    priority = channels_repo.normalize_rss_priority(str(channel.get("rss_priority") or "normal"))
    multiplier = RSS_POLL_PRIORITY_MULTIPLIERS[priority] * RSS_POLL_OUTCOME_MULTIPLIERS[outcome]
    return int(
        min(
            RSS_POLL_MAX_INTERVAL_SECONDS,
            max(RSS_POLL_MIN_INTERVAL_SECONDS, round(base_seconds * multiplier)),
        )
    )


def _rss_cache_from_channel(channel: dict[str, Any], feed_mode: str) -> dict[str, str]:
    cache_feed_mode = str(channel.get("rss_cache_feed_mode") or "")
    if cache_feed_mode != feed_mode:
        return {}
    return {
        "etag": str(channel.get("rss_last_etag") or ""),
        "last_modified": str(channel.get("rss_last_modified") or ""),
        "feed_mode": cache_feed_mode,
    }


async def run_rss_poller(state: AppState) -> None:
    polling_interval_sec = _get_base_poll_interval_seconds(state)
    deactivate_threshold = state.config.rss_channel_deactivate_after_fails
    abort_threshold = state.config.rss_consecutive_error_abort_threshold
    jitter_ratio = 0.3
    check_step_seconds = 5

    logger.info(
        "event=rss.poller_started worker=rss interval=%sm deactivate_after=%s abort_after=%s fetcher=%s",
        state.config.polling_interval_minutes,
        deactivate_threshold,
        abort_threshold,
        _get_fetcher_mode(state),
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
            except TimeoutError:
                pass
            continue

        base_delay = max(
            float(getattr(state.config, "rss_inter_channel_delay_seconds", 0.0)),
            polling_interval_sec / channel_count,
        )
        if manual_trigger:
            base_delay = max(
                float(getattr(state.config, "rss_inter_channel_delay_seconds", 0.0)),
                base_delay * 0.25,
            )

        logger.info(
            "event=rss.cycle_started worker=rss channels=%d delay=%.1fs manual=%s",
            channel_count,
            base_delay,
            manual_trigger,
            extra={"event": "rss.cycle_started", "worker": "rss"},
        )

        policy = await settings_repo.get_policy_settings(state.db)
        feed_mode = str(policy.get("rss_feed_mode", "long_form_only"))
        lookback_days = max(1, int(policy["rss_bootstrap_lookback_days"]))
        lower_bound = state.started_at - timedelta(days=lookback_days)

        consecutive_errors = 0
        polled = 0
        inserted_total = 0
        cycle_start = time.monotonic()
        visited_channel_ids: list[str] = []

        for _ in range(channel_count):
            if not await settings_repo.is_worker_enabled(state.db, "rss"):
                break

            if state.poll_now_event.is_set():
                state.poll_now_event.clear()
                manual_trigger = True
                base_delay = max(
                    float(getattr(state.config, "rss_inter_channel_delay_seconds", 0.0)),
                    (polling_interval_sec / channel_count) * 0.25,
                )

            channel = await channels_repo.pick_next_rss_channel(
                state.db,
                include_not_due=manual_trigger,
                exclude_channel_ids=visited_channel_ids,
            )
            if channel is None:
                break
            visited_channel_ids.append(str(channel["channel_id"]))

            try:
                ok, count = await _poll_single_channel(
                    state,
                    channel=channel,
                    deactivate_threshold=deactivate_threshold,
                    feed_mode=feed_mode,
                    lower_bound=lower_bound,
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
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(state.poll_now_event.wait(), timeout=max(0.1, delay))

        if polled > 0:
            logger.info(
                "event=rss.cycle_completed worker=rss polled=%d inserted=%d",
                polled,
                inserted_total,
                extra={"event": "rss.cycle_completed", "worker": "rss"},
            )

        elapsed = time.monotonic() - cycle_start
        next_due_delay = await channels_repo.get_seconds_until_next_rss_poll(state.db)
        remaining = max(1.0, polling_interval_sec - elapsed)
        if next_due_delay is not None:
            remaining = min(remaining, max(1.0, next_due_delay))

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
                except TimeoutError:
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
    feed_mode: str,
    lower_bound: datetime,
) -> tuple[bool, int]:
    """단일 채널 RSS 폴링. (성공여부, 삽입건수) 반환.

    feed_mode/lower_bound는 호출자(사이클 단위)에서 한 번만 조회/계산해
    여기로 넘긴다. 채널마다 DB를 다시 읽지 않는다.
    """
    channel_id = channel["channel_id"]
    channel_name = channel.get("channel_name") or channel_id
    fetcher_mode = _get_fetcher_mode(state)

    if fetcher_mode == RSS_FETCHER_MODE_YT_DLP:
        result = await _poll_channel_with_yt_dlp(
            state,
            channel=channel,
            lower_bound=lower_bound,
            reason="mode",
        )
        if result is not None:
            return result
        await channels_repo.touch_rss_last_polled_at(
            state.db,
            channel_id,
            interval_seconds=_rss_interval_seconds(state, channel, "error"),
        )
        return False, 0

    cache = _rss_cache_from_channel(channel, feed_mode) or state.rss_cache.get(channel_id, {})
    if cache.get("feed_mode", "") == feed_mode:
        etag = cache.get("etag") or None
        last_modified = cache.get("last_modified") or None
    else:
        etag, last_modified = None, None

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
            if fetcher_mode == RSS_FETCHER_MODE_RSS_THEN_YT_DLP:
                result = await _poll_channel_with_yt_dlp(
                    state,
                    channel=channel,
                    lower_bound=lower_bound,
                    reason="rss_404",
                )
                if result is not None:
                    return result
                await channels_repo.touch_rss_last_polled_at(
                    state.db,
                    channel_id,
                    interval_seconds=_rss_interval_seconds(state, channel, "error"),
                )
                logger.warning(
                    "event=rss.feed_not_found_suppressed worker=rss channel_id=%s reason=yt_dlp_fallback_failed",
                    channel_id,
                    extra={
                        "event": "rss.feed_not_found_suppressed",
                        "worker": "rss",
                        "code": "404",
                    },
                )
                return False, 0
            streak = await channels_repo.increment_rss_fail_streak(
                state.db,
                channel_id,
                interval_seconds=_rss_interval_seconds(state, channel, "not_found"),
            )
            if streak >= deactivate_threshold:
                await channels_repo.deactivate_channel(state.db, channel_id)
                await alerts_repo.create_system_alert(
                    state.db,
                    alert_type=alerts_repo.ALERT_TYPE_RSS_CHANNEL_NOT_FOUND,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    message=f"RSS feed returned 404 Not Found ({streak} consecutive failures). Channel deactivated.",
                )
                invalidate_alert_groups_cache(state)
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
        if fetcher_mode == RSS_FETCHER_MODE_RSS_THEN_YT_DLP:
            result = await _poll_channel_with_yt_dlp(
                state,
                channel=channel,
                lower_bound=lower_bound,
                reason=f"rss_http_{status_code or 'unknown'}",
            )
            if result is not None:
                return result
        await channels_repo.touch_rss_last_polled_at(
            state.db,
            channel_id,
            interval_seconds=_rss_interval_seconds(state, channel, "error"),
        )
        logger.warning(
            "event=rss.fetch_failed worker=rss channel_id=%s status=%s",
            channel_id,
            status_code,
            extra={"event": "rss.fetch_failed", "worker": "rss", "code": str(status_code or "-")},
        )
        return False, 0
    except RSSParseError:
        if fetcher_mode == RSS_FETCHER_MODE_RSS_THEN_YT_DLP:
            result = await _poll_channel_with_yt_dlp(
                state,
                channel=channel,
                lower_bound=lower_bound,
                reason="rss_parse_error",
            )
            if result is not None:
                return result
        await channels_repo.touch_rss_last_polled_at(
            state.db,
            channel_id,
            interval_seconds=_rss_interval_seconds(state, channel, "error"),
        )
        logger.warning(
            "event=rss.parse_failed worker=rss channel_id=%s",
            channel_id,
            extra={"event": "rss.parse_failed", "worker": "rss", "code": "rss_parse_error"},
        )
        return False, 0
    except Exception:
        if fetcher_mode == RSS_FETCHER_MODE_RSS_THEN_YT_DLP:
            result = await _poll_channel_with_yt_dlp(
                state,
                channel=channel,
                lower_bound=lower_bound,
                reason="rss_exception",
            )
            if result is not None:
                return result
        await channels_repo.touch_rss_last_polled_at(
            state.db,
            channel_id,
            interval_seconds=_rss_interval_seconds(state, channel, "error"),
        )
        logger.exception(
            "event=rss.fetch_exception worker=rss channel_id=%s",
            channel_id,
            extra={"event": "rss.fetch_exception", "worker": "rss"},
        )
        return False, 0

    state.rss_cache[channel_id] = {
        "etag": new_etag or "",
        "last_modified": new_last_modified or "",
        "feed_mode": feed_mode,
    }

    channel_inserted = await _insert_feed_entries(
        state,
        channel=channel,
        entries=entries,
        lower_bound=lower_bound,
    )
    outcome = "new" if channel_inserted > 0 else "unchanged"
    await channels_repo.mark_rss_poll_success(
        state.db,
        channel_id,
        interval_seconds=_rss_interval_seconds(state, channel, outcome),
        etag=new_etag,
        last_modified=new_last_modified,
        feed_mode=feed_mode,
    )
    return True, channel_inserted


async def _poll_channel_with_yt_dlp(
    state: AppState,
    *,
    channel: dict[str, Any],
    lower_bound: datetime,
    reason: str,
) -> tuple[bool, int] | None:
    channel_id = channel["channel_id"]
    yt_dlp_service = getattr(state, "yt_dlp_service", None)
    if yt_dlp_service is None:
        logger.warning(
            "event=rss.yt_dlp_fallback_unavailable worker=rss channel_id=%s reason=%s",
            channel_id,
            reason,
            extra={"event": "rss.yt_dlp_fallback_unavailable", "worker": "rss"},
        )
        return None

    try:
        entries = await yt_dlp_service.fetch_channel_feed(channel_id)
    except (YtDlpFeedError, TimeoutError) as exc:
        logger.warning(
            "event=rss.yt_dlp_fallback_failed worker=rss channel_id=%s reason=%s error_type=%s",
            channel_id,
            reason,
            type(exc).__name__,
            extra={"event": "rss.yt_dlp_fallback_failed", "worker": "rss"},
        )
        return None
    except Exception:
        logger.exception(
            "event=rss.yt_dlp_fallback_exception worker=rss channel_id=%s reason=%s",
            channel_id,
            reason,
            extra={"event": "rss.yt_dlp_fallback_exception", "worker": "rss"},
        )
        return None

    channel_inserted = await _insert_feed_entries(
        state,
        channel=channel,
        entries=entries,
        lower_bound=lower_bound,
    )
    outcome = "new" if channel_inserted > 0 else "unchanged"
    await channels_repo.mark_rss_poll_success(
        state.db,
        channel_id,
        interval_seconds=_rss_interval_seconds(state, channel, outcome),
        etag=str(channel.get("rss_last_etag") or ""),
        last_modified=str(channel.get("rss_last_modified") or ""),
        feed_mode=str(channel.get("rss_cache_feed_mode") or "") or None,
    )
    logger.info(
        "event=rss.yt_dlp_fallback_succeeded worker=rss channel_id=%s reason=%s entries=%d inserted=%d",
        channel_id,
        reason,
        len(entries),
        channel_inserted,
        extra={"event": "rss.yt_dlp_fallback_succeeded", "worker": "rss"},
    )
    return True, channel_inserted


async def _insert_feed_entries(
    state: AppState,
    *,
    channel: dict[str, Any],
    entries: list[dict[str, str]],
    lower_bound: datetime,
) -> int:
    channel_id = channel["channel_id"]
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

    if channel_inserted > 0:
        invalidate_retention_notice_cache(state)

    return channel_inserted


async def poll_once(state: AppState, *, inter_channel_delay: float = 0.0) -> int:
    """기존 호환용: 활성 채널 전체를 순회하여 폴링. 삽입 건수 반환."""
    channels = await channels_repo.list_active_channels(state.db)
    config = getattr(state, "config", None)
    deactivate_threshold = getattr(config, "rss_channel_deactivate_after_fails", 3) if config else 3
    total_inserted = 0

    policy = await settings_repo.get_policy_settings(state.db)
    feed_mode = str(policy.get("rss_feed_mode", "long_form_only"))
    lookback_days = max(1, int(policy["rss_bootstrap_lookback_days"]))
    started_at = getattr(state, "started_at", datetime.now(UTC))
    lower_bound = started_at - timedelta(days=lookback_days)

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

        _ok, count = await _poll_single_channel(
            state,
            channel=channel,
            deactivate_threshold=deactivate_threshold,
            feed_mode=feed_mode,
            lower_bound=lower_bound,
        )
        total_inserted += count

    return total_inserted
