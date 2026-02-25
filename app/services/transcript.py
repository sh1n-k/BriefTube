from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from youtube_transcript_api import YouTubeTranscriptApi


class TranscriptService:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.api = YouTubeTranscriptApi()

    async def fetch_transcript(self, video_id: str) -> tuple[str, str | None, str]:
        transcript_obj = await asyncio.to_thread(self.api.fetch, video_id)
        raw_data = transcript_obj.to_raw_data()
        raw_text = "\n".join(segment.get("text", "").strip() for segment in raw_data if segment.get("text"))
        language = getattr(transcript_obj, "language_code", None)
        is_generated = bool(getattr(transcript_obj, "is_generated", False))
        source_type = "auto" if is_generated else "manual"
        return raw_text, language, source_type

    async def download_thumbnail(self, video_id: str, thumbnail_dir: str) -> str | None:
        directory = Path(thumbnail_dir)
        directory.mkdir(parents=True, exist_ok=True)

        url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        target = directory / f"{video_id}.jpg"

        response = await self.client.get(url, timeout=15)
        if response.status_code >= 400:
            return None
        target.write_bytes(response.content)
        return str(target)
