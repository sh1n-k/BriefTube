from __future__ import annotations

import asyncio
import logging

from app import repository
from app.state import AppState

logger = logging.getLogger(__name__)


def _is_no_subtitle_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = [
        "transcript",
        "not found",
        "unavailable",
        "disabled",
        "no transcripts",
    ]
    return any(marker in message for marker in markers)


async def run_transcript_fetcher(state: AppState) -> None:
    while True:
        try:
            pending = await repository.pop_pending_transcript_videos(state.db, limit=3)
            if not pending:
                await asyncio.sleep(5)
                continue

            for video in pending:
                video_id = video["video_id"]
                try:
                    raw_text, language, source_type = await state.transcript_service.fetch_transcript(video_id)
                    thumbnail_path = await state.transcript_service.download_thumbnail(
                        video_id,
                        state.config.thumbnail_dir,
                    )
                    await repository.save_transcript(
                        state.db,
                        video_id=video_id,
                        raw_text=raw_text,
                        language=language,
                        source_type=source_type,
                        thumbnail_path=thumbnail_path,
                    )
                except Exception as exc:
                    if _is_no_subtitle_error(exc):
                        await repository.mark_no_subtitle(state.db, video_id)
                        logger.info("No subtitle for video_id=%s", video_id)
                        continue
                    logger.exception("Transcript fetch failed. video_id=%s", video_id)
        except Exception:
            logger.exception("Transcript worker loop failed")
            await asyncio.sleep(5)
