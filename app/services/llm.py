from __future__ import annotations

from datetime import datetime, timezone

import httpx


class OpenClawClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_url: str,
        api_key: str,
        timeout_seconds: int,
    ):
        self.client = client
        self.api_url = api_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def restructure(self, source_title: str, transcript_text: str) -> dict[str, str]:
        if not self.api_url:
            preview_lines = [line.strip() for line in transcript_text.splitlines() if line.strip()][:3]
            lead = "\n".join(preview_lines) if preview_lines else "No transcript summary available"
            return {
                "title": f"[Draft] {source_title}",
                "lead": lead,
                "body": transcript_text[:4000],
                "fact_box": "{}",
                "timestamps": "[]",
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "title": source_title,
            "transcript": transcript_text,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }

        response = await self.client.post(
            self.api_url,
            json=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

        data = response.json()
        if isinstance(data, dict) and {"title", "lead", "body"}.issubset(set(data.keys())):
            return {
                "title": str(data["title"]),
                "lead": str(data["lead"]),
                "body": str(data["body"]),
                "fact_box": str(data.get("fact_box", "{}")),
                "timestamps": str(data.get("timestamps", "[]")),
            }

        article = data.get("article") if isinstance(data, dict) else None
        if isinstance(article, dict) and {"title", "lead", "body"}.issubset(set(article.keys())):
            return {
                "title": str(article["title"]),
                "lead": str(article["lead"]),
                "body": str(article["body"]),
                "fact_box": str(article.get("fact_box", "{}")),
                "timestamps": str(article.get("timestamps", "[]")),
            }

        raise ValueError("Unsupported OpenClaw response schema")
