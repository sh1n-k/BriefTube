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
    transcript_fetch_batch_size: int = 1
    transcript_request_interval_seconds: int = 15
    transcript_idle_sleep_seconds: int = 5
    transcript_retry_base_delay_seconds: int = 120
    transcript_retry_max_delay_seconds: int = 3600
    transcript_retry_max_attempts: int = 8
    transcript_fetch_timeout_seconds: int = 45
    transcript_jitter_ratio: float = 0.30
    transcript_adaptive_enabled: bool = True
    transcript_adaptive_max_factor: float = 8.0
    transcript_hard_cooldown_base_seconds: int = 300
    transcript_hard_cooldown_max_seconds: int = 3600
    transcript_recovery_success_window: int = 5
    transcript_general_error_slowdown_multiplier: float = 1.25
    log_level: str = "INFO"
    log_to_file: bool = True
    log_dir: str = "./logs"
    log_file_name: str = "brieftube.log"
    log_file_max_bytes: int = 5 * 1024 * 1024
    log_file_backup_count: int = 5


def _parse_scalar(value: str) -> str | int | bool:
    raw = value.strip().strip('"').strip("'")
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    if raw.isdigit():
        return int(raw)
    return raw


def _parse_env_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _parse_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
        transcript_fetch_batch_size=int(
            file_values.get("transcript_fetch_batch_size", base.transcript_fetch_batch_size)
        ),
        transcript_request_interval_seconds=int(
            file_values.get(
                "transcript_request_interval_seconds",
                base.transcript_request_interval_seconds,
            )
        ),
        transcript_idle_sleep_seconds=int(
            file_values.get("transcript_idle_sleep_seconds", base.transcript_idle_sleep_seconds)
        ),
        transcript_retry_base_delay_seconds=int(
            file_values.get(
                "transcript_retry_base_delay_seconds",
                base.transcript_retry_base_delay_seconds,
            )
        ),
        transcript_retry_max_delay_seconds=int(
            file_values.get(
                "transcript_retry_max_delay_seconds",
                base.transcript_retry_max_delay_seconds,
            )
        ),
        transcript_retry_max_attempts=int(
            file_values.get(
                "transcript_retry_max_attempts",
                base.transcript_retry_max_attempts,
            )
        ),
        transcript_fetch_timeout_seconds=int(
            file_values.get(
                "transcript_fetch_timeout_seconds",
                base.transcript_fetch_timeout_seconds,
            )
        ),
        transcript_jitter_ratio=_parse_float(
            file_values.get("transcript_jitter_ratio", base.transcript_jitter_ratio),
            base.transcript_jitter_ratio,
        ),
        transcript_adaptive_enabled=_parse_env_bool(
            file_values.get("transcript_adaptive_enabled", base.transcript_adaptive_enabled)
        ),
        transcript_adaptive_max_factor=_parse_float(
            file_values.get("transcript_adaptive_max_factor", base.transcript_adaptive_max_factor),
            base.transcript_adaptive_max_factor,
        ),
        transcript_hard_cooldown_base_seconds=int(
            file_values.get(
                "transcript_hard_cooldown_base_seconds",
                base.transcript_hard_cooldown_base_seconds,
            )
        ),
        transcript_hard_cooldown_max_seconds=int(
            file_values.get(
                "transcript_hard_cooldown_max_seconds",
                base.transcript_hard_cooldown_max_seconds,
            )
        ),
        transcript_recovery_success_window=int(
            file_values.get(
                "transcript_recovery_success_window",
                base.transcript_recovery_success_window,
            )
        ),
        transcript_general_error_slowdown_multiplier=_parse_float(
            file_values.get(
                "transcript_general_error_slowdown_multiplier",
                base.transcript_general_error_slowdown_multiplier,
            ),
            base.transcript_general_error_slowdown_multiplier,
        ),
        log_level=str(file_values.get("log_level", base.log_level)),
        log_to_file=_parse_env_bool(file_values.get("log_to_file", base.log_to_file)),
        log_dir=str(file_values.get("log_dir", base.log_dir)),
        log_file_name=str(file_values.get("log_file_name", base.log_file_name)),
        log_file_max_bytes=int(file_values.get("log_file_max_bytes", base.log_file_max_bytes)),
        log_file_backup_count=int(file_values.get("log_file_backup_count", base.log_file_backup_count)),
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
    cfg.transcript_fetch_batch_size = int(
        os.getenv("TRANSCRIPT_FETCH_BATCH_SIZE", cfg.transcript_fetch_batch_size)
    )
    cfg.transcript_request_interval_seconds = int(
        os.getenv(
            "TRANSCRIPT_REQUEST_INTERVAL_SECONDS",
            cfg.transcript_request_interval_seconds,
        )
    )
    cfg.transcript_idle_sleep_seconds = int(
        os.getenv("TRANSCRIPT_IDLE_SLEEP_SECONDS", cfg.transcript_idle_sleep_seconds)
    )
    cfg.transcript_retry_base_delay_seconds = int(
        os.getenv(
            "TRANSCRIPT_RETRY_BASE_DELAY_SECONDS",
            cfg.transcript_retry_base_delay_seconds,
        )
    )
    cfg.transcript_retry_max_delay_seconds = int(
        os.getenv(
            "TRANSCRIPT_RETRY_MAX_DELAY_SECONDS",
            cfg.transcript_retry_max_delay_seconds,
        )
    )
    cfg.transcript_retry_max_attempts = int(
        os.getenv(
            "TRANSCRIPT_RETRY_MAX_ATTEMPTS",
            cfg.transcript_retry_max_attempts,
        )
    )
    cfg.transcript_fetch_timeout_seconds = int(
        os.getenv(
            "TRANSCRIPT_FETCH_TIMEOUT_SECONDS",
            cfg.transcript_fetch_timeout_seconds,
        )
    )
    cfg.transcript_jitter_ratio = _parse_float(
        os.getenv("TRANSCRIPT_JITTER_RATIO", cfg.transcript_jitter_ratio),
        cfg.transcript_jitter_ratio,
    )
    cfg.transcript_adaptive_enabled = _parse_env_bool(
        os.getenv("TRANSCRIPT_ADAPTIVE_ENABLED", str(cfg.transcript_adaptive_enabled))
    )
    cfg.transcript_adaptive_max_factor = _parse_float(
        os.getenv("TRANSCRIPT_ADAPTIVE_MAX_FACTOR", cfg.transcript_adaptive_max_factor),
        cfg.transcript_adaptive_max_factor,
    )
    cfg.transcript_hard_cooldown_base_seconds = int(
        os.getenv(
            "TRANSCRIPT_HARD_COOLDOWN_BASE_SECONDS",
            cfg.transcript_hard_cooldown_base_seconds,
        )
    )
    cfg.transcript_hard_cooldown_max_seconds = int(
        os.getenv(
            "TRANSCRIPT_HARD_COOLDOWN_MAX_SECONDS",
            cfg.transcript_hard_cooldown_max_seconds,
        )
    )
    cfg.transcript_recovery_success_window = int(
        os.getenv(
            "TRANSCRIPT_RECOVERY_SUCCESS_WINDOW",
            cfg.transcript_recovery_success_window,
        )
    )
    cfg.transcript_general_error_slowdown_multiplier = _parse_float(
        os.getenv(
            "TRANSCRIPT_GENERAL_ERROR_SLOWDOWN_MULTIPLIER",
            cfg.transcript_general_error_slowdown_multiplier,
        ),
        cfg.transcript_general_error_slowdown_multiplier,
    )
    cfg.log_level = os.getenv("LOG_LEVEL", cfg.log_level).upper()
    cfg.log_to_file = _parse_env_bool(os.getenv("LOG_TO_FILE", str(cfg.log_to_file)))
    cfg.log_dir = os.getenv("LOG_DIR", cfg.log_dir)
    cfg.log_file_name = os.getenv("LOG_FILE_NAME", cfg.log_file_name)
    cfg.log_file_max_bytes = int(os.getenv("LOG_FILE_MAX_BYTES", cfg.log_file_max_bytes))
    cfg.log_file_backup_count = int(os.getenv("LOG_FILE_BACKUP_COUNT", cfg.log_file_backup_count))

    cfg.transcript_fetch_batch_size = max(1, cfg.transcript_fetch_batch_size)
    cfg.transcript_request_interval_seconds = max(1, cfg.transcript_request_interval_seconds)
    cfg.transcript_idle_sleep_seconds = max(1, cfg.transcript_idle_sleep_seconds)
    cfg.transcript_retry_base_delay_seconds = max(1, cfg.transcript_retry_base_delay_seconds)
    cfg.transcript_retry_max_delay_seconds = max(
        cfg.transcript_retry_base_delay_seconds,
        cfg.transcript_retry_max_delay_seconds,
    )
    cfg.transcript_retry_max_attempts = max(1, cfg.transcript_retry_max_attempts)
    cfg.transcript_fetch_timeout_seconds = max(1, cfg.transcript_fetch_timeout_seconds)
    cfg.transcript_jitter_ratio = max(0.0, min(0.5, cfg.transcript_jitter_ratio))
    cfg.transcript_adaptive_max_factor = max(1.0, cfg.transcript_adaptive_max_factor)
    cfg.transcript_hard_cooldown_base_seconds = max(1, cfg.transcript_hard_cooldown_base_seconds)
    cfg.transcript_hard_cooldown_max_seconds = max(
        cfg.transcript_hard_cooldown_base_seconds,
        cfg.transcript_hard_cooldown_max_seconds,
    )
    cfg.transcript_recovery_success_window = max(1, cfg.transcript_recovery_success_window)
    cfg.transcript_general_error_slowdown_multiplier = max(
        1.0,
        cfg.transcript_general_error_slowdown_multiplier,
    )
    return cfg
