from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.parse import quote

import httpx


CHANNEL_ID_RE = re.compile(r"UC[-_A-Za-z0-9]{22}")
YOUTUBE_URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/[^\s]+", re.IGNORECASE)
HANDLE_RE = re.compile(r"^@[A-Za-z0-9_.-]+$")

CHANNEL_ID_PATTERNS = [
    re.compile(r'"channelId":"(UC[-_A-Za-z0-9]{22})"'),
    re.compile(r'"externalId":"(UC[-_A-Za-z0-9]{22})"'),
    re.compile(r'"browseId":"(UC[-_A-Za-z0-9]{22})"'),
]

OG_TITLE_PATTERN = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.IGNORECASE)
CHANNEL_RENDERER_PATTERN = re.compile(
    r'"channelRenderer":\{[^{}]*?"channelId":"(UC[-_A-Za-z0-9]{22})".*?"title":\{"simpleText":"(.*?)"\}',
    re.DOTALL,
)


@dataclass(slots=True)
class ChannelCandidate:
    channel_id: str
    channel_name: str
    channel_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "channel_url": self.channel_url,
        }


class ChannelResolverService:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def resolve_input(self, raw_input: str) -> dict:
        source = raw_input.strip()
        if not source:
            return {"input": raw_input, "status": "failed", "reason": "empty input"}

        if CHANNEL_ID_RE.fullmatch(source):
            candidate = await self._resolve_from_channel_id(source)
            if candidate:
                return {
                    "input": raw_input,
                    "status": "resolved",
                    "resolved": candidate.to_dict(),
                }
            return {"input": raw_input, "status": "failed", "reason": "channel id not found"}

        if HANDLE_RE.fullmatch(source):
            resolved = await self._resolve_from_url(f"https://www.youtube.com/{source}")
            if resolved:
                return {
                    "input": raw_input,
                    "status": "resolved",
                    "resolved": resolved.to_dict(),
                }
            return {"input": raw_input, "status": "failed", "reason": "handle not found"}

        if YOUTUBE_URL_RE.match(source):
            resolved = await self._resolve_from_url(source)
            if resolved:
                return {
                    "input": raw_input,
                    "status": "resolved",
                    "resolved": resolved.to_dict(),
                }
            return {"input": raw_input, "status": "failed", "reason": "url cannot be resolved"}

        candidates = await self._search_by_channel_name(source)
        if not candidates:
            return {
                "input": raw_input,
                "status": "failed",
                "reason": "no candidates found",
            }
        if len(candidates) == 1:
            return {
                "input": raw_input,
                "status": "resolved",
                "resolved": candidates[0].to_dict(),
            }
        return {
            "input": raw_input,
            "status": "needs_selection",
            "candidates": [candidate.to_dict() for candidate in candidates],
        }

    async def _resolve_from_channel_id(self, channel_id: str) -> ChannelCandidate | None:
        url = f"https://www.youtube.com/channel/{channel_id}"
        return await self._resolve_from_url(url)

    async def _resolve_from_url(self, url: str) -> ChannelCandidate | None:
        response = await self.client.get(url, follow_redirects=True, timeout=20)
        if response.status_code >= 400:
            return None

        html = response.text
        channel_id = self._extract_channel_id(html) or self._extract_channel_id(str(response.url))
        if not channel_id:
            return None

        channel_name = self._extract_channel_name(html) or channel_id
        return ChannelCandidate(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_url=f"https://www.youtube.com/channel/{channel_id}",
        )

    async def _search_by_channel_name(self, query: str) -> list[ChannelCandidate]:
        url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIQAg%253D%253D"
        response = await self.client.get(url, timeout=20)
        if response.status_code >= 400:
            return []

        html = response.text
        seen: set[str] = set()
        candidates: list[ChannelCandidate] = []

        for channel_id, raw_name in CHANNEL_RENDERER_PATTERN.findall(html):
            if channel_id in seen:
                continue
            seen.add(channel_id)
            name = self._decode_json_string(raw_name) or channel_id
            candidates.append(
                ChannelCandidate(
                    channel_id=channel_id,
                    channel_name=name,
                    channel_url=f"https://www.youtube.com/channel/{channel_id}",
                )
            )
            if len(candidates) >= 5:
                break

        return candidates

    def _extract_channel_id(self, text: str) -> str | None:
        for pattern in CHANNEL_ID_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
        direct = CHANNEL_ID_RE.search(text)
        return direct.group(0) if direct else None

    def _extract_channel_name(self, html: str) -> str | None:
        match = OG_TITLE_PATTERN.search(html)
        if not match:
            return None
        value = match.group(1).strip()
        if value.endswith(" - YouTube"):
            value = value[:-10].strip()
        return value

    def _decode_json_string(self, value: str) -> str:
        try:
            return json.loads(f'"{value}"')
        except json.JSONDecodeError:
            return value
