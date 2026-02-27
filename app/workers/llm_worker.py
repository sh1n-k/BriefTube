from __future__ import annotations

import asyncio
import logging
import time

from app import repository
from app.state import AppState

logger = logging.getLogger(__name__)


async def run_llm_queue_worker(state: AppState) -> None:
    next_missing_config_log_at = 0.0
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

            if not state.config.openclaw_api_url:
                alert_created = await repository.ensure_llm_config_missing_alert(state.db)
                now = time.monotonic()
                if now >= next_missing_config_log_at:
                    logger.warning(
                        "event=llm.config_missing worker=llm video_id=%s alert_created=%s",
                        video_id,
                        alert_created,
                        extra={"event": "llm.config_missing", "worker": "llm"},
                    )
                    next_missing_config_log_at = now + 60.0
                await asyncio.sleep(10)
                continue

            await repository.clear_llm_config_missing_alert_flag(state.db)
            marked = await repository.mark_restructure_processing(state.db, video_id)
            if marked == 0:
                await asyncio.sleep(1)
                continue

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
                logger.info(
                    "event=llm.restructure_succeeded worker=llm video_id=%s",
                    video_id,
                    extra={"event": "llm.restructure_succeeded", "worker": "llm"},
                )
            except Exception as exc:
                next_status, affected = await repository.mark_restructure_failed(
                    state.db,
                    video_id=video_id,
                    retry_count=int(candidate["retry_count"]),
                    max_retry_count=state.config.max_retry_count,
                )
                if affected == 0:
                    logger.warning(
                        "event=llm.restructure_failed_stale_skip worker=llm video_id=%s",
                        video_id,
                        extra={"event": "llm.restructure_failed_stale_skip", "worker": "llm"},
                    )
                else:
                    logger.exception(
                        "event=llm.restructure_failed worker=llm video_id=%s next_status=%s error_type=%s",
                        video_id,
                        next_status,
                        exc.__class__.__name__,
                        extra={"event": "llm.restructure_failed", "worker": "llm"},
                    )
        except Exception:
            logger.exception(
                "event=llm.worker_loop_failed worker=llm",
                extra={"event": "llm.worker_loop_failed", "worker": "llm"},
            )
            await asyncio.sleep(5)
