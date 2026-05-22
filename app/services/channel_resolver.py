from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

CHANNEL_ID_RE = re.compile(r"UC[-_A-Za-z0-9]{22}")
YOUTUBE_URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/[^\s]+", re.IGNORECASE)
HANDLE_RE = re.compile(r"^@[\w._-]+$", re.UNICODE)
HANDLE_IN_URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/(@[^/?#\"'&]+)", re.IGNORECASE)
CHANNEL_ID_PATTERNS = [
    re.compile(r'"externalId":"(UC[-_A-Za-z0-9]{22})"'),
    re.compile(r'"browseId":"(UC[-_A-Za-z0-9]{22})"'),
    re.compile(r'"channelId":"(UC[-_A-Za-z0-9]{22})"'),
]
OG_TITLE_PATTERN = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.IGNORECASE)
OG_DESCRIPTION_PATTERN = re.compile(
    r'<meta\s+property="og:description"\s+content="([^"]*)"', re.IGNORECASE
)
OG_IMAGE_PATTERN = re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.IGNORECASE)
CANONICAL_LINK_PATTERN = re.compile(r'<link\s+rel="canonical"\s+href="([^"]+)"', re.IGNORECASE)
CANONICAL_BASE_URL_PATTERN = re.compile(r'"canonicalBaseUrl":"(\/@[^"]+)"', re.IGNORECASE)
HTML_LANG_PATTERN = re.compile(r"<html[^>]*\slang=\"([^\"]+)\"", re.IGNORECASE)
CHANNEL_RENDERER_PATTERN = re.compile(
    r'"channelRenderer":\{[^{}]*?"channelId":"(UC[-_A-Za-z0-9]{22})".*?"title":\{"simpleText":"(.*?)"\}',
    re.DOTALL,
)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


@dataclass(slots=True)
class ChannelCandidate:
    channel_id: str
    channel_name: str
    channel_url: str
    channel_handle: str | None = None
    channel_description: str | None = None
    channel_thumbnail_url: str | None = None
    channel_language_hint: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "channel_url": self.channel_url,
            "channel_handle": self.channel_handle,
            "channel_description": self.channel_description,
            "channel_thumbnail_url": self.channel_thumbnail_url,
            "channel_language_hint": self.channel_language_hint,
        }


@dataclass(slots=True)
class ChannelMetadataResult:
    ok: bool
    channel_id: str
    channel_name: str | None = None
    channel_handle: str | None = None
    channel_url_canonical: str | None = None
    channel_thumbnail_url: str | None = None
    channel_description: str | None = None
    channel_language_hint: str | None = None
    error: str | None = None
    http_status: int | None = None
    is_rate_limited: bool = False


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

    async def fetch_channel_metadata(self, channel_id: str) -> ChannelMetadataResult:
        normalized_channel_id = str(channel_id or "").strip()
        if not CHANNEL_ID_RE.fullmatch(normalized_channel_id):
            return ChannelMetadataResult(
                ok=False,
                channel_id=normalized_channel_id,
                error="invalid_channel_id",
            )
        url = f"https://www.youtube.com/channel/{normalized_channel_id}"
        page = await self._fetch_page(url)
        status_value = page.get("status")
        http_status = status_value if isinstance(status_value, int) else None
        if page.get("error"):
            return ChannelMetadataResult(
                ok=False,
                channel_id=normalized_channel_id,
                error=str(page.get("error")),
                http_status=http_status,
                is_rate_limited=bool(page.get("is_rate_limited")),
            )
        html = str(page.get("html") or "")
        final_url = str(page.get("final_url") or url)
        resolved_channel_id = (
            self._extract_channel_id(html)
            or self._extract_channel_id(final_url)
            or normalized_channel_id
        )
        canonical_url = self._extract_canonical_url(html) or final_url
        return ChannelMetadataResult(
            ok=True,
            channel_id=resolved_channel_id,
            channel_name=self._extract_channel_name(html) or resolved_channel_id,
            channel_handle=self._extract_handle(html, canonical_url),
            channel_url_canonical=canonical_url,
            channel_thumbnail_url=self._extract_og_image(html),
            channel_description=self._extract_channel_description(html),
            channel_language_hint=self._extract_language_hint(html),
            http_status=http_status,
        )

    async def _resolve_from_channel_id(self, channel_id: str) -> ChannelCandidate | None:
        return await self._resolve_from_url(f"https://www.youtube.com/channel/{channel_id}")

    async def _resolve_from_url(self, url: str) -> ChannelCandidate | None:
        page = await self._fetch_page(url)
        if page.get("error"):
            return None
        html = str(page.get("html") or "")
        final_url = str(page.get("final_url") or url)
        channel_id = self._extract_channel_id(html) or self._extract_channel_id(final_url)
        if not channel_id:
            return None
        canonical_url = (
            self._extract_canonical_url(html) or f"https://www.youtube.com/channel/{channel_id}"
        )
        return ChannelCandidate(
            channel_id=channel_id,
            channel_name=self._extract_channel_name(html) or channel_id,
            channel_url=f"https://www.youtube.com/channel/{channel_id}",
            channel_handle=self._extract_handle(html, canonical_url),
            channel_description=self._extract_channel_description(html),
            channel_thumbnail_url=self._extract_og_image(html),
            channel_language_hint=self._extract_language_hint(html),
        )

    async def _fetch_page(self, url: str) -> dict[str, object]:
        try:
            response = await self.client.get(
                url,
                follow_redirects=True,
                timeout=20,
                headers=REQUEST_HEADERS,
            )
        except httpx.TimeoutException:
            return {"error": "timeout"}
        except httpx.HTTPError as exc:
            return {"error": f"http_error:{exc.__class__.__name__}"}
        status = int(response.status_code)
        if status >= 400:
            return {
                "error": f"http_{status}",
                "status": status,
                "is_rate_limited": status in {403, 429},
            }
        return {
            "status": status,
            "final_url": str(response.url),
            "html": response.text,
        }

    async def _search_by_channel_name(self, query: str) -> list[ChannelCandidate]:
        url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIQAg%253D%253D"
        try:
            response = await self.client.get(url, timeout=20, headers=REQUEST_HEADERS)
        except httpx.HTTPError:
            return []
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
        return value or None

    def _extract_channel_description(self, html: str) -> str | None:
        match = OG_DESCRIPTION_PATTERN.search(html)
        if not match:
            return None
        value = match.group(1).strip()
        return value or None

    def _extract_og_image(self, html: str) -> str | None:
        match = OG_IMAGE_PATTERN.search(html)
        if not match:
            return None
        value = match.group(1).strip()
        return value or None

    def _extract_canonical_url(self, html: str) -> str | None:
        link_match = CANONICAL_LINK_PATTERN.search(html)
        if link_match:
            value = link_match.group(1).strip()
            if value:
                return value
        base_match = CANONICAL_BASE_URL_PATTERN.search(html)
        if not base_match:
            return None
        suffix = base_match.group(1).strip()
        if not suffix:
            return None
        return f"https://www.youtube.com{suffix}"

    def _extract_handle(self, html: str, canonical_url: str | None = None) -> str | None:
        if canonical_url:
            canonical_match = HANDLE_IN_URL_RE.match(canonical_url)
            if canonical_match:
                return canonical_match.group(1)
        link_match = CANONICAL_LINK_PATTERN.search(html)
        if link_match:
            match = HANDLE_IN_URL_RE.match(link_match.group(1).strip())
            if match:
                return match.group(1)
        base_match = CANONICAL_BASE_URL_PATTERN.search(html)
        if base_match:
            raw = base_match.group(1).strip()
            if raw.startswith("/@"):
                return raw[1:]
        return None

    def _extract_language_hint(self, html: str) -> str | None:
        match = HTML_LANG_PATTERN.search(html)
        if not match:
            return None
        value = match.group(1).strip().lower()
        if not value:
            return None
        if len(value) > 12:
            return value[:12]
        return value

    def _decode_json_string(self, value: str) -> str:
        try:
            return json.loads(f'"{value}"')
        except json.JSONDecodeError:
            return value
