from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(slots=True)
class AppConfig:
    polling_interval_minutes: int = 15
    max_retry_count: int = 3
    openclaw_api_url: str = ""
    openclaw_api_key: str = ""
    openclaw_timeout_seconds: int = 120
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    thumbnail_dir: str = "./thumbnails"
    db_path: str = "./data.db"
    rss_timeout_seconds: int = 20
    http_timeout_seconds: int = 30


def _parse_scalar(value: str) -> str | int | bool:
    raw = value.strip().strip('"').strip("'")
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    if raw.isdigit():
        return int(raw)
    return raw


def _load_simple_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}

    parsed: dict[str, object] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        parsed[key.strip()] = _parse_scalar(value)
    return parsed


def load_config() -> AppConfig:
    config_file = os.getenv("APP_CONFIG_FILE")

    base = AppConfig()
    file_values = _load_simple_yaml(Path(config_file)) if config_file else {}

    cfg = AppConfig(
        polling_interval_minutes=int(file_values.get("polling_interval_minutes", base.polling_interval_minutes)),
        max_retry_count=int(file_values.get("max_retry_count", base.max_retry_count)),
        openclaw_api_url=str(file_values.get("openclaw_api_url", base.openclaw_api_url)),
        openclaw_api_key=str(file_values.get("openclaw_api_key", base.openclaw_api_key)),
        openclaw_timeout_seconds=int(file_values.get("openclaw_timeout_seconds", base.openclaw_timeout_seconds)),
        telegram_bot_token=str(file_values.get("telegram_bot_token", base.telegram_bot_token)),
        telegram_chat_id=str(file_values.get("telegram_chat_id", base.telegram_chat_id)),
        thumbnail_dir=str(file_values.get("thumbnail_dir", base.thumbnail_dir)),
        db_path=str(file_values.get("db_path", base.db_path)),
        rss_timeout_seconds=int(file_values.get("rss_timeout_seconds", base.rss_timeout_seconds)),
        http_timeout_seconds=int(file_values.get("http_timeout_seconds", base.http_timeout_seconds)),
    )

    cfg.polling_interval_minutes = int(os.getenv("POLLING_INTERVAL_MINUTES", cfg.polling_interval_minutes))
    cfg.max_retry_count = int(os.getenv("MAX_RETRY_COUNT", cfg.max_retry_count))
    cfg.openclaw_api_url = os.getenv("OPENCLAW_API_URL", cfg.openclaw_api_url)
    cfg.openclaw_api_key = os.getenv("OPENCLAW_API_KEY", cfg.openclaw_api_key)
    cfg.openclaw_timeout_seconds = int(os.getenv("OPENCLAW_TIMEOUT_SECONDS", cfg.openclaw_timeout_seconds))
    cfg.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", cfg.telegram_bot_token)
    cfg.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", cfg.telegram_chat_id)
    cfg.thumbnail_dir = os.getenv("THUMBNAIL_DIR", cfg.thumbnail_dir)
    cfg.db_path = os.getenv("DB_PATH", cfg.db_path)
    cfg.rss_timeout_seconds = int(os.getenv("RSS_TIMEOUT_SECONDS", cfg.rss_timeout_seconds))
    cfg.http_timeout_seconds = int(os.getenv("HTTP_TIMEOUT_SECONDS", cfg.http_timeout_seconds))
    return cfg
