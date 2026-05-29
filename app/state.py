from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import aiosqlite
import httpx

from app.config import AppConfig
from app.services.channel_resolver import ChannelResolverService
from app.services.llm import UnifiedLlmClient
from app.services.llm_capabilities import LlmCapabilityProbe
from app.services.rss import RSSService
from app.services.telegram import TelegramNotifier
from app.services.transcript import TranscriptService
from app.services.yt_dlp_feed import YtDlpFeedService

ALERT_GROUPS_UI_CACHE_KEY = "alert_groups:5"
RETENTION_NOTICE_UI_CACHE_KEY_PREFIX = "retention_notice_count:"


def invalidate_ui_cache(
    runtime: object,
    *,
    exact_keys: Iterable[str] = (),
    key_prefixes: Iterable[str] = (),
) -> None:
    raw_cache = getattr(runtime, "ui_cache", None)
    if not isinstance(raw_cache, dict):
        return
    cache = cast(dict[str, tuple[float, Any]], raw_cache)

    for key in exact_keys:
        cache.pop(key, None)

    prefixes = tuple(str(prefix) for prefix in key_prefixes)
    if not prefixes:
        return
    for key in list(cache):
        if key.startswith(prefixes):
            cache.pop(key, None)


def invalidate_alert_groups_cache(runtime: object) -> None:
    invalidate_ui_cache(runtime, exact_keys=(ALERT_GROUPS_UI_CACHE_KEY,))


def invalidate_retention_notice_cache(runtime: object) -> None:
    invalidate_ui_cache(runtime, key_prefixes=(RETENTION_NOTICE_UI_CACHE_KEY_PREFIX,))


@dataclass(slots=True)
class AppState:
    config: AppConfig
    db: aiosqlite.Connection
    http_client: httpx.AsyncClient
    rss_service: RSSService
    yt_dlp_service: YtDlpFeedService
    transcript_service: TranscriptService
    channel_resolver: ChannelResolverService
    llm_client: UnifiedLlmClient
    llm_capability_probe: LlmCapabilityProbe
    telegram_notifier: TelegramNotifier
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    poll_now_event: asyncio.Event = field(default_factory=asyncio.Event)
    llm_wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    download_wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    manual_article_wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    manual_transcript_wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    channel_metadata_wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    transcript_worker_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    notification_queue: asyncio.Queue[dict[str, str]] = field(
        default_factory=lambda: asyncio.Queue()
    )
    rss_cache: dict[str, dict[str, str]] = field(default_factory=lambda: {})
    ui_cache: dict[str, tuple[float, Any]] = field(default_factory=lambda: {})

    def invalidate_ui_cache(
        self,
        *,
        exact_keys: Iterable[str] = (),
        key_prefixes: Iterable[str] = (),
    ) -> None:
        invalidate_ui_cache(self, exact_keys=exact_keys, key_prefixes=key_prefixes)

    def invalidate_alert_groups_cache(self) -> None:
        invalidate_alert_groups_cache(self)

    def invalidate_retention_notice_cache(self) -> None:
        invalidate_retention_notice_cache(self)
