from __future__ import annotations

import asyncio
import logging

from app import repository
from app.state import AppState

logger = logging.getLogger(__name__)


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


async def run_telegram_notifier(state: AppState) -> None:
    while True:
        if not state.telegram_notifier.is_configured():
            await asyncio.sleep(5)
            continue
        if not await repository.is_worker_enabled(state.db, "notifier"):
            await asyncio.sleep(3)
            continue

        try:
            try:
                item = await asyncio.wait_for(state.notification_queue.get(), timeout=3)
            except asyncio.TimeoutError:
                continue
            batch = [item]

            await asyncio.sleep(1)
            while True:
                try:
                    batch.append(state.notification_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            message = _format_batch_message(batch)
            response = await state.telegram_notifier.send(message)
            if not response.get("ok", False):
                logger.warning("Telegram send skipped/failed: %s", response)
        except Exception:
            logger.exception("Notifier loop failed")
            await asyncio.sleep(3)
