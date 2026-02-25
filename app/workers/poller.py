from __future__ import annotations

import asyncio
import logging

from app import repository
from app.state import AppState

logger = logging.getLogger(__name__)


async def run_rss_poller(state: AppState) -> None:
    interval_seconds = max(60, state.config.polling_interval_minutes * 60)

    while True:
        try:
            inserted = await poll_once(state)
            if inserted:
                logger.info("RSS poll completed. inserted=%s", inserted)
        except Exception:
            logger.exception("RSS poll failed")

        try:
            await asyncio.wait_for(state.poll_now_event.wait(), timeout=interval_seconds)
            state.poll_now_event.clear()
            logger.info("Manual poll trigger consumed")
        except asyncio.TimeoutError:
            continue


async def poll_once(state: AppState) -> int:
    channels = await repository.list_active_channels(state.db)
    total_inserted = 0

    for channel in channels:
        channel_id = channel["channel_id"]
        cache = state.rss_cache.get(channel_id, {})
        etag = cache.get("etag")
        last_modified = cache.get("last_modified")

        entries, new_etag, new_last_modified = await state.rss_service.fetch_channel_feed(
            channel_id=channel_id,
            etag=etag,
            last_modified=last_modified,
        )

        state.rss_cache[channel_id] = {
            "etag": new_etag or "",
            "last_modified": new_last_modified or "",
        }

        watermark = channel.get("last_seen_published_at")
        max_published = watermark

        for entry in entries:
            published = entry["published"]
            if not repository.is_newer_published(published, watermark):
                continue

            inserted = await repository.insert_video_if_absent(
                state.db,
                video_id=entry["video_id"],
                channel_id=channel_id,
                title=entry["title"],
                upload_time=published,
            )
            if inserted:
                total_inserted += 1

            if max_published is None or repository.is_newer_published(published, max_published):
                max_published = published

        if max_published and max_published != watermark:
            await repository.update_channel_watermark(state.db, channel_id, max_published)

    return total_inserted
