from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
from datetime import datetime, timezone

import aiosqlite
import httpx

from app.config import AppConfig
from app.services.channel_resolver import ChannelResolverService
from app.services.llm import OpenClawClient
from app.services.rss import RSSService
from app.services.telegram import TelegramNotifier
from app.services.transcript import TranscriptService


@dataclass(slots=True)
class AppState:
    config: AppConfig
    db: aiosqlite.Connection
    http_client: httpx.AsyncClient
    rss_service: RSSService
    transcript_service: TranscriptService
    channel_resolver: ChannelResolverService
    llm_client: OpenClawClient
    telegram_notifier: TelegramNotifier
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    poll_now_event: asyncio.Event = field(default_factory=asyncio.Event)
    download_wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    notification_queue: asyncio.Queue[dict[str, str]] = field(default_factory=asyncio.Queue)
    rss_cache: dict[str, dict[str, str]] = field(default_factory=dict)
