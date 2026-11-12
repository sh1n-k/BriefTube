from __future__ import annotations

import logging
from typing import Protocol

import aiosqlite

from app.config import AppConfig
from app.database import database_transaction
from app.repositories import channels as channels_repo
from app.services.thumbnail_files import cleanup_thumbnail_files

logger = logging.getLogger(__name__)


class ChannelDeletionRuntime(Protocol):
    config: AppConfig
    db: aiosqlite.Connection
    rss_cache: dict[str, dict[str, str]]

    def invalidate_retention_notice_cache(self) -> None: ...


async def delete_channels_and_cleanup(
    runtime: ChannelDeletionRuntime,
    channel_ids: list[str],
) -> channels_repo.ChannelDeletionResult:
    async with database_transaction(runtime.config.db_path) as db:
        result = await channels_repo.delete_channels_with_related_data(
            db,
            channel_ids,
            commit=False,
        )
    if result["deleted_channels"] > 0:
        logger.info(
            "event=channels.deleted count=%s channels=%s videos=%s transcripts=%s articles=%s",
            result["deleted_channels"],
            ", ".join(
                f"{channel_id}({name})"
                for channel_id, name in result["deleted_channel_names"].items()
            ),
            result["deleted_videos"],
            result["deleted_transcripts"],
            result["deleted_articles"],
            extra={"event": "channels.deleted"},
        )
    if result["deleted_videos"] > 0:
        runtime.invalidate_retention_notice_cache()
    cleanup_thumbnail_files(result["thumbnail_paths"], runtime.config.thumbnail_dir)
    for channel_id in channel_ids:
        runtime.rss_cache.pop(channel_id, None)
    return result
