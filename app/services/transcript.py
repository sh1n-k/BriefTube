from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi

from app.services.article_timestamps import format_timestamp_label, strip_position_markers
from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    default_transcript_request_headers,
    merge_with_default_headers,
)

# Auto-dubbed audio tracks expose ASR captions whose URL carries this variant.
# The original-audio ASR track does not, so it identifies the spoken language.
_DUBBED_ASR_URL_MARKER = "variant=timing-optimized"
TIMED_TEXT_MARKER_INTERVAL_SECONDS = 30


class TranscriptFetchResult(NamedTuple):
    raw_text: str
    language: str | None
    source_type: str
    timed_text: str | None


def build_timed_text(
    segments: Iterable[Mapping[str, Any]],
    *,
    interval_seconds: int = TIMED_TEXT_MARKER_INTERVAL_SECONDS,
) -> str | None:
    """Join segments like raw_text, prefixing a [MM:SS] marker at most every interval."""
    lines: list[str] = []
    last_marker_at: float | None = None
    has_marker = False
    for segment in segments:
        # Caption text that already looks like a marker would widen timestamp grounding.
        text = strip_position_markers(str(segment.get("text") or "")).strip()
        if not text:
            continue
        start_raw = segment.get("start")
        try:
            start = float(start_raw) if start_raw is not None else None
        except (TypeError, ValueError):
            start = None
        if start is not None and (
            last_marker_at is None or start - last_marker_at >= interval_seconds
        ):
            last_marker_at = start
            has_marker = True
            text = f"[{format_timestamp_label(start)}] {text}"
        lines.append(text)
    if not has_marker:
        return None
    return "\n".join(lines)


def _primary_language(code: str | None) -> str:
    return str(code or "").strip().lower().split("-")[0]


def _is_dubbed_asr_track(track: Any) -> bool:
    url = str(getattr(track, "_url", "") or "")
    return _DUBBED_ASR_URL_MARKER in url


class _TimeoutSession(Session):
    def __init__(self, default_timeout_seconds: int):
        super().__init__()
        self._default_timeout_seconds = max(1, int(default_timeout_seconds))

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self._default_timeout_seconds)
        return super().request(method, url, **kwargs)


class TranscriptService:
    def __init__(self, *, request_timeout_seconds: int = 45):
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
        manual_tracks = [
            track for track in tracks if not bool(getattr(track, "is_generated", False))
        ]
        generated_tracks = [
            track for track in tracks if bool(getattr(track, "is_generated", False))
        ]
        original_asr = next(
            (track for track in generated_tracks if not _is_dubbed_asr_track(track)), None
        )
        if original_asr is not None:
            # Prefer a manual track in the spoken language, then its ASR track.
            # Manual tracks in other languages are translations uploaded by the creator.
            original_language = _primary_language(getattr(original_asr, "language_code", None))
            same_language_manual = next(
                (
                    track
                    for track in manual_tracks
                    if _primary_language(getattr(track, "language_code", None)) == original_language
                ),
                None,
            )
            return same_language_manual or original_asr
        if manual_tracks:
            return manual_tracks[0]
        return tracks[0]

    def _select_preferred_track(self, transcript_list: Any, preferred_language: str) -> Any:
        tracks = list(transcript_list)
        matches = [
            track
            for track in tracks
            if str(getattr(track, "language_code", "") or "").lower() == preferred_language
        ]
        usable = [track for track in matches if not _is_dubbed_asr_track(track)]
        if usable:
            manual = next(
                (track for track in usable if not bool(getattr(track, "is_generated", False))),
                None,
            )
            return manual or usable[0]
        if matches:
            # The stored language came from an auto-dub track; recover the spoken language.
            fallback = self._select_default_track(tracks)
            return matches[0] if _is_dubbed_asr_track(fallback) else fallback
        return transcript_list.find_transcript([preferred_language])

    def _fetch_default_language_track(self, api: YouTubeTranscriptApi, video_id: str) -> Any:
        transcript_list = api.list(video_id)
        selected = self._select_default_track(transcript_list)
        return selected.fetch()

    def _fetch_transcript_sync(
        self,
        video_id: str,
        preferred_language: str | None,
    ) -> TranscriptFetchResult:
        session = _TimeoutSession(self._request_timeout_seconds)
        try:
            request_headers = self.get_transcript_request_headers()
            session.headers.update(request_headers)
            api = YouTubeTranscriptApi(http_client=session)
            session.headers.update(request_headers)
            preferred = (preferred_language or "").strip().lower()
            if preferred:
                transcript_list = api.list(video_id)
                transcript_obj = self._select_preferred_track(transcript_list, preferred).fetch()
            else:
                transcript_obj = self._fetch_default_language_track(api, video_id)
            raw_data = transcript_obj.to_raw_data()
            raw_text = "\n".join(
                segment.get("text", "").strip() for segment in raw_data if segment.get("text")
            )
            language = getattr(transcript_obj, "language_code", None)
            is_generated = bool(getattr(transcript_obj, "is_generated", False))
            source_type = "auto" if is_generated else "manual"
            return TranscriptFetchResult(
                raw_text=raw_text,
                language=language,
                source_type=source_type,
                timed_text=build_timed_text(raw_data),
            )
        finally:
            session.close()

    async def fetch_transcript(
        self,
        video_id: str,
        preferred_language: str | None = None,
    ) -> TranscriptFetchResult:
        return await asyncio.to_thread(
            self._fetch_transcript_sync,
            video_id,
            preferred_language,
        )
