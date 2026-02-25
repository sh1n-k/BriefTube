from __future__ import annotations

from xml.etree.ElementTree import ParseError, fromstring

import httpx


NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


class RSSService:
    def __init__(self, client: httpx.AsyncClient, timeout_seconds: int = 20):
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def fetch_channel_feed(
        self,
        channel_id: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> tuple[list[dict[str, str]], str | None, str | None]:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        response = await self.client.get(url, headers=headers, timeout=self.timeout_seconds)
        if response.status_code == 304:
            return [], etag, last_modified
        response.raise_for_status()

        latest_etag = response.headers.get("ETag")
        latest_last_modified = response.headers.get("Last-Modified")

        try:
            root = fromstring(response.text)
        except ParseError:
            return [], latest_etag, latest_last_modified

        entries: list[dict[str, str]] = []
        for entry in root.findall("atom:entry", NAMESPACES):
            video_id = entry.findtext("yt:videoId", default="", namespaces=NAMESPACES).strip()
            title = entry.findtext("atom:title", default="", namespaces=NAMESPACES).strip()
            published = entry.findtext("atom:published", default="", namespaces=NAMESPACES).strip()
            thumbnail_url = ""
            thumbnail = entry.find("media:group/media:thumbnail", NAMESPACES)
            if thumbnail is not None:
                thumbnail_url = (thumbnail.get("url") or "").strip()

            if not video_id or not title or not published:
                continue

            entries.append(
                {
                    "video_id": video_id,
                    "title": title,
                    "published": published,
                    "thumbnail_url": thumbnail_url,
                }
            )

        return entries, latest_etag, latest_last_modified
