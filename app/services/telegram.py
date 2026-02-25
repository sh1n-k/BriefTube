from __future__ import annotations

import httpx


class TelegramNotifier:
    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient):
        self.chat_id = chat_id
        self.client = client
        self.url = self.BASE_URL.format(token=token) if token else ""

    async def send(self, text: str, parse_mode: str = "HTML") -> dict:
        if not self.url or not self.chat_id:
            return {"ok": False, "skipped": True, "reason": "telegram not configured"}

        response = await self.client.post(
            self.url,
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
