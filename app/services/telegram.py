from __future__ import annotations

import httpx
import os

from app.config import AppConfig

TELEGRAM_SOURCE_ENV = "env"
TELEGRAM_SOURCE_DB = "db"
TELEGRAM_SOURCE_CONFIG = "config"
TELEGRAM_SOURCE_NONE = "none"


def _pick_telegram_value(*candidates: tuple[str, str]) -> tuple[str, str]:
    for source, raw_value in candidates:
        value = str(raw_value or "").strip()
        if value:
            return value, source
    return "", TELEGRAM_SOURCE_NONE


def _pick_telegram_bundle(*candidates: tuple[str, str, str]) -> tuple[str, str, str]:
    for source, raw_token, raw_chat_id in candidates:
        token = str(raw_token or "").strip()
        chat_id = str(raw_chat_id or "").strip()
        if token and chat_id:
            return token, chat_id, source
    return "", "", TELEGRAM_SOURCE_NONE


def mask_telegram_secret(
    value: str,
    *,
    preserve_start: int = 4,
    preserve_end: int = 4,
) -> str:
    safe = str(value or "").strip()
    if not safe:
        return ""
    if len(safe) <= preserve_start + preserve_end:
        if len(safe) <= 2:
            return "••••"
        return f"{safe[:1]}…{safe[-1:]}"
    return f"{safe[:preserve_start]}…{safe[-preserve_end:]}"


def resolve_telegram_settings(
    config: AppConfig,
    *,
    stored_bot_token: str = "",
    stored_chat_id: str = "",
) -> dict[str, object]:
    env_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    env_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    config_bot_token = "" if env_bot_token else str(config.telegram_bot_token or "").strip()
    config_chat_id = "" if env_chat_id else str(config.telegram_chat_id or "").strip()
    stored_token = str(stored_bot_token or "").strip()
    stored_chat = str(stored_chat_id or "").strip()

    effective_bot_token, effective_chat_id, effective_source = _pick_telegram_bundle(
        (TELEGRAM_SOURCE_ENV, env_bot_token, env_chat_id),
        (TELEGRAM_SOURCE_DB, stored_token, stored_chat),
        (TELEGRAM_SOURCE_CONFIG, config_bot_token, config_chat_id),
    )

    return {
        "stored_bot_token": stored_token,
        "stored_chat_id": stored_chat,
        "stored_bot_token_preview": mask_telegram_secret(stored_token),
        "stored_chat_id_preview": mask_telegram_secret(stored_chat),
        "bot_token_stored": bool(stored_token),
        "chat_id_stored": bool(stored_chat),
        "effective_bot_token": effective_bot_token,
        "effective_chat_id": effective_chat_id,
        "effective_bot_token_preview": mask_telegram_secret(effective_bot_token),
        "effective_chat_id_preview": mask_telegram_secret(effective_chat_id),
        "bot_token_source": effective_source,
        "chat_id_source": effective_source,
        "configured": bool(effective_bot_token and effective_chat_id),
        "override_active": effective_source in {TELEGRAM_SOURCE_ENV, TELEGRAM_SOURCE_CONFIG},
    }


def build_telegram_settings_payload(
    config: AppConfig,
    *,
    stored_bot_token: str = "",
    stored_chat_id: str = "",
) -> dict[str, object]:
    resolved = resolve_telegram_settings(
        config,
        stored_bot_token=stored_bot_token,
        stored_chat_id=stored_chat_id,
    )
    return {
        "configured": bool(resolved["configured"]),
        "override_active": bool(resolved["override_active"]),
        "bot_token_stored": bool(resolved["bot_token_stored"]),
        "chat_id_stored": bool(resolved["chat_id_stored"]),
        "stored_bot_token_preview": str(resolved["stored_bot_token_preview"]),
        "stored_chat_id_preview": str(resolved["stored_chat_id_preview"]),
        "effective_bot_token_preview": str(resolved["effective_bot_token_preview"]),
        "effective_chat_id_preview": str(resolved["effective_chat_id_preview"]),
        "bot_token_source": str(resolved["bot_token_source"]),
        "chat_id_source": str(resolved["chat_id_source"]),
    }


def configure_telegram_notifier(
    notifier: "TelegramNotifier",
    config: AppConfig,
    *,
    stored_bot_token: str = "",
    stored_chat_id: str = "",
) -> dict[str, object]:
    resolved = resolve_telegram_settings(
        config,
        stored_bot_token=stored_bot_token,
        stored_chat_id=stored_chat_id,
    )
    notifier.configure(
        token=str(resolved["effective_bot_token"]),
        chat_id=str(resolved["effective_chat_id"]),
    )
    return resolved


class TelegramNotifier:
    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient):
        self.client = client
        self.chat_id = ""
        self.url = ""
        self.configure(token=token, chat_id=chat_id)

    def configure(self, *, token: str, chat_id: str) -> None:
        safe_token = str(token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.url = self.BASE_URL.format(token=safe_token) if safe_token else ""

    def is_configured(self) -> bool:
        return bool(self.url and self.chat_id)

    async def send(self, text: str, parse_mode: str = "HTML") -> dict:
        if not self.is_configured():
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
