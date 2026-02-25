from __future__ import annotations

import asyncio
import logging

from app import repository
from app.state import AppState

logger = logging.getLogger(__name__)


async def run_llm_queue_worker(state: AppState) -> None:
    while True:
        if not await repository.is_worker_enabled(state.db, "llm"):
            await asyncio.sleep(5)
            continue

        try:
            candidate = await repository.pop_llm_candidate(state.db, state.config.max_retry_count)
            if not candidate:
                await asyncio.sleep(5)
                continue

            video_id = candidate["video_id"]
            await repository.mark_restructure_processing(state.db, video_id)

            try:
                article = await state.llm_client.restructure(
                    source_title=candidate["title"],
                    transcript_text=candidate["raw_text"],
                )
                await repository.save_article(
                    state.db,
                    video_id=video_id,
                    title=article["title"],
                    lead=article["lead"],
                    body=article["body"],
                    fact_box=article.get("fact_box"),
                    timestamps=article.get("timestamps"),
                )
                await state.notification_queue.put(
                    {
                        "video_id": video_id,
                        "title": article["title"],
                        "lead": article["lead"],
                    }
                )
            except Exception:
                next_status = await repository.mark_restructure_failed(
                    state.db,
                    video_id=video_id,
                    retry_count=int(candidate["retry_count"]),
                    max_retry_count=state.config.max_retry_count,
                )
                logger.exception(
                    "LLM restructure failed. video_id=%s next_status=%s",
                    video_id,
                    next_status,
                )
        except Exception:
            logger.exception("LLM worker loop failed")
            await asyncio.sleep(5)
