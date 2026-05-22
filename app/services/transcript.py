from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi

from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    default_transcript_request_headers,
    merge_with_default_headers,
)


class _TimeoutSession(Session):
    def __init__(self, default_timeout_seconds: int):
        super().__init__()
        self._default_timeout_seconds = max(1, int(default_timeout_seconds))

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self._default_timeout_seconds)
        return super().request(method, url, **kwargs)


class TranscriptService:
    def __init__(self, client: httpx.AsyncClient, *, request_timeout_seconds: int = 45):
        self.client = client
        self._request_headers = default_transcript_request_headers()
        self._request_timeout_seconds = max(1, int(request_timeout_seconds))

    def apply_transcript_request_headers(self, values: dict[str, str]) -> None:
        merged = merge_with_default_headers(values)
        self._request_headers = {key: merged[key] for key in TRANSCRIPT_REQUEST_HEADER_KEYS}

    def get_transcript_request_headers(self) -> dict[str, str]:
        defaults = default_transcript_request_headers()
        return {
            key: str(self._request_headers.get(key) or defaults[key]).strip() or defaults[key]
            for key in TRANSCRIPT_REQUEST_HEADER_KEYS
        }

    def _select_default_track(self, transcript_list: Any) -> Any:
        tracks = list(transcript_list)
        if not tracks:
            raise RuntimeError("No transcript tracks found")
        manual = next(
            (track for track in tracks if not bool(getattr(track, "is_generated", False))), None
        )
        return manual or tracks[0]

    def _fetch_default_language_track(self, api: YouTubeTranscriptApi, video_id: str) -> Any:
        transcript_list = api.list(video_id)
        selected = self._select_default_track(transcript_list)
        return selected.fetch()

    def _fetch_transcript_sync(
        self,
        video_id: str,
        preferred_language: str | None,
    ) -> tuple[str, str | None, str]:
        session = _TimeoutSession(self._request_timeout_seconds)
        try:
            request_headers = self.get_transcript_request_headers()
            session.headers.update(request_headers)
            api = YouTubeTranscriptApi(http_client=session)
            session.headers.update(request_headers)
            preferred = (preferred_language or "").strip().lower()
            if preferred:
                transcript_obj = api.fetch(video_id, [preferred])
            else:
                transcript_obj = self._fetch_default_language_track(api, video_id)
            raw_data = transcript_obj.to_raw_data()
            raw_text = "\n".join(
                segment.get("text", "").strip() for segment in raw_data if segment.get("text")
            )
            language = getattr(transcript_obj, "language_code", None)
            is_generated = bool(getattr(transcript_obj, "is_generated", False))
            source_type = "auto" if is_generated else "manual"
            return raw_text, language, source_type
        finally:
            session.close()

    async def fetch_transcript(
        self,
        video_id: str,
        preferred_language: str | None = None,
    ) -> tuple[str, str | None, str]:
        return await asyncio.to_thread(
            self._fetch_transcript_sync,
            video_id,
            preferred_language,
        )

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
