from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import os

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppConfig:
    env: str = "prod"
    polling_interval_minutes: int = 15
    max_retry_count: int = 3
    llm_timeout_seconds: int = 120
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    thumbnail_dir: str = "./thumbnails"
    db_path: str = "./data.db"
    rss_timeout_seconds: int = 20
    rss_inter_channel_delay_seconds: float = 2.0
    http_timeout_seconds: int = 30
    download_dir: str = "./downloads"
    download_max_concurrent: int = 2
    download_timeout_seconds: int = 1800
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
    transcript_channel_min_interval_seconds: int = 180
    transcript_channel_pick_lookahead: int = 20
    transcript_channel_hard_cooldown_seconds: int = 900
    transcript_breaker_half_open_probe_count: int = 1
    transcript_worker_lease_enabled: bool = True
    transcript_worker_lease_ttl_seconds: int = 45
    log_level: str = "AUTO"
    log_to_file: bool = True
    log_dir: str = "./logs"
    log_file_name: str = "brieftube.log"
    log_file_max_bytes: int = 10 * 1024 * 1024
    log_file_backup_count: int = 10
    rss_channel_deactivate_after_fails: int = 3
    rss_consecutive_error_abort_threshold: int = 5
    log_noise_window_seconds: int = 60
    log_noise_suppress_threshold: int = 1
    log_dependency_level: str = "WARNING"


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
        if isinstance(value, str) and value.strip():
            _logger.warning(
                "event=config.parse_float_fallback raw_value=%r default=%s",
                value,
                default,
            )
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
        env=str(file_values.get("env", base.env)),
        polling_interval_minutes=int(file_values.get("polling_interval_minutes", base.polling_interval_minutes)),
        max_retry_count=int(file_values.get("max_retry_count", base.max_retry_count)),
        llm_timeout_seconds=int(file_values.get("llm_timeout_seconds", base.llm_timeout_seconds)),
        telegram_bot_token=str(file_values.get("telegram_bot_token", base.telegram_bot_token)),
        telegram_chat_id=str(file_values.get("telegram_chat_id", base.telegram_chat_id)),
        thumbnail_dir=str(file_values.get("thumbnail_dir", base.thumbnail_dir)),
        db_path=str(file_values.get("db_path", base.db_path)),
        rss_timeout_seconds=int(file_values.get("rss_timeout_seconds", base.rss_timeout_seconds)),
        rss_inter_channel_delay_seconds=_parse_float(
            file_values.get("rss_inter_channel_delay_seconds", base.rss_inter_channel_delay_seconds),
            base.rss_inter_channel_delay_seconds,
        ),
        http_timeout_seconds=int(file_values.get("http_timeout_seconds", base.http_timeout_seconds)),
        download_dir=str(file_values.get("download_dir", base.download_dir)),
        download_max_concurrent=int(file_values.get("download_max_concurrent", base.download_max_concurrent)),
        download_timeout_seconds=int(file_values.get("download_timeout_seconds", base.download_timeout_seconds)),
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
        transcript_channel_min_interval_seconds=int(
            file_values.get(
                "transcript_channel_min_interval_seconds",
                base.transcript_channel_min_interval_seconds,
            )
        ),
        transcript_channel_pick_lookahead=int(
            file_values.get(
                "transcript_channel_pick_lookahead",
                base.transcript_channel_pick_lookahead,
            )
        ),
        transcript_channel_hard_cooldown_seconds=int(
            file_values.get(
                "transcript_channel_hard_cooldown_seconds",
                base.transcript_channel_hard_cooldown_seconds,
            )
        ),
        transcript_breaker_half_open_probe_count=int(
            file_values.get(
                "transcript_breaker_half_open_probe_count",
                base.transcript_breaker_half_open_probe_count,
            )
        ),
        transcript_worker_lease_enabled=_parse_env_bool(
            file_values.get(
                "transcript_worker_lease_enabled",
                base.transcript_worker_lease_enabled,
            )
        ),
        transcript_worker_lease_ttl_seconds=int(
            file_values.get(
                "transcript_worker_lease_ttl_seconds",
                base.transcript_worker_lease_ttl_seconds,
            )
        ),
        rss_channel_deactivate_after_fails=int(
            file_values.get("rss_channel_deactivate_after_fails", base.rss_channel_deactivate_after_fails)
        ),
        rss_consecutive_error_abort_threshold=int(
            file_values.get("rss_consecutive_error_abort_threshold", base.rss_consecutive_error_abort_threshold)
        ),
        log_level=str(file_values.get("log_level", base.log_level)),
        log_to_file=_parse_env_bool(file_values.get("log_to_file", base.log_to_file)),
        log_dir=str(file_values.get("log_dir", base.log_dir)),
        log_file_name=str(file_values.get("log_file_name", base.log_file_name)),
        log_file_max_bytes=int(file_values.get("log_file_max_bytes", base.log_file_max_bytes)),
        log_file_backup_count=int(file_values.get("log_file_backup_count", base.log_file_backup_count)),
        log_noise_window_seconds=int(file_values.get("log_noise_window_seconds", base.log_noise_window_seconds)),
        log_noise_suppress_threshold=int(
            file_values.get("log_noise_suppress_threshold", base.log_noise_suppress_threshold)
        ),
        log_dependency_level=str(file_values.get("log_dependency_level", base.log_dependency_level)),
    )

    cfg.env = os.getenv("ENV", cfg.env).strip().lower() or "prod"
    cfg.polling_interval_minutes = int(os.getenv("POLLING_INTERVAL_MINUTES", cfg.polling_interval_minutes))
    cfg.max_retry_count = int(os.getenv("MAX_RETRY_COUNT", cfg.max_retry_count))
    cfg.llm_timeout_seconds = int(os.getenv("LLM_TIMEOUT_SECONDS", cfg.llm_timeout_seconds))
    cfg.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", cfg.telegram_bot_token)
    cfg.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", cfg.telegram_chat_id)
    cfg.thumbnail_dir = os.getenv("THUMBNAIL_DIR", cfg.thumbnail_dir)
    cfg.db_path = os.getenv("DB_PATH", cfg.db_path)
    cfg.rss_timeout_seconds = int(os.getenv("RSS_TIMEOUT_SECONDS", cfg.rss_timeout_seconds))
    cfg.rss_inter_channel_delay_seconds = _parse_float(
        os.getenv("RSS_INTER_CHANNEL_DELAY_SECONDS", cfg.rss_inter_channel_delay_seconds),
        cfg.rss_inter_channel_delay_seconds,
    )
    cfg.http_timeout_seconds = int(os.getenv("HTTP_TIMEOUT_SECONDS", cfg.http_timeout_seconds))
    cfg.download_dir = os.getenv("DOWNLOAD_DIR", cfg.download_dir)
    cfg.download_max_concurrent = int(os.getenv("DOWNLOAD_MAX_CONCURRENT", cfg.download_max_concurrent))
    cfg.download_timeout_seconds = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", cfg.download_timeout_seconds))
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
    cfg.transcript_channel_min_interval_seconds = int(
        os.getenv(
            "TRANSCRIPT_CHANNEL_MIN_INTERVAL_SECONDS",
            cfg.transcript_channel_min_interval_seconds,
        )
    )
    cfg.transcript_channel_pick_lookahead = int(
        os.getenv(
            "TRANSCRIPT_CHANNEL_PICK_LOOKAHEAD",
            cfg.transcript_channel_pick_lookahead,
        )
    )
    cfg.transcript_channel_hard_cooldown_seconds = int(
        os.getenv(
            "TRANSCRIPT_CHANNEL_HARD_COOLDOWN_SECONDS",
            cfg.transcript_channel_hard_cooldown_seconds,
        )
    )
    cfg.transcript_breaker_half_open_probe_count = int(
        os.getenv(
            "TRANSCRIPT_BREAKER_HALF_OPEN_PROBE_COUNT",
            cfg.transcript_breaker_half_open_probe_count,
        )
    )
    cfg.transcript_worker_lease_enabled = _parse_env_bool(
        os.getenv("TRANSCRIPT_WORKER_LEASE_ENABLED", str(cfg.transcript_worker_lease_enabled))
    )
    cfg.transcript_worker_lease_ttl_seconds = int(
        os.getenv(
            "TRANSCRIPT_WORKER_LEASE_TTL_SECONDS",
            cfg.transcript_worker_lease_ttl_seconds,
        )
    )
    cfg.rss_channel_deactivate_after_fails = int(
        os.getenv("RSS_CHANNEL_DEACTIVATE_AFTER_FAILS", cfg.rss_channel_deactivate_after_fails)
    )
    cfg.rss_consecutive_error_abort_threshold = int(
        os.getenv("RSS_CONSECUTIVE_ERROR_ABORT_THRESHOLD", cfg.rss_consecutive_error_abort_threshold)
    )
    cfg.log_level = os.getenv("LOG_LEVEL", cfg.log_level).strip().upper() or "AUTO"
    cfg.log_to_file = _parse_env_bool(os.getenv("LOG_TO_FILE", str(cfg.log_to_file)))
    cfg.log_dir = os.getenv("LOG_DIR", cfg.log_dir)
    cfg.log_file_name = os.getenv("LOG_FILE_NAME", cfg.log_file_name)
    cfg.log_file_max_bytes = int(os.getenv("LOG_FILE_MAX_BYTES", cfg.log_file_max_bytes))
    cfg.log_file_backup_count = int(os.getenv("LOG_FILE_BACKUP_COUNT", cfg.log_file_backup_count))
    cfg.log_noise_window_seconds = int(os.getenv("LOG_NOISE_WINDOW_SECONDS", cfg.log_noise_window_seconds))
    cfg.log_noise_suppress_threshold = int(
        os.getenv("LOG_NOISE_SUPPRESS_THRESHOLD", cfg.log_noise_suppress_threshold)
    )
    cfg.log_dependency_level = os.getenv("LOG_DEPENDENCY_LEVEL", cfg.log_dependency_level).strip().upper()

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
    cfg.transcript_channel_min_interval_seconds = max(0, cfg.transcript_channel_min_interval_seconds)
    cfg.transcript_channel_pick_lookahead = max(1, cfg.transcript_channel_pick_lookahead)
    cfg.transcript_channel_hard_cooldown_seconds = max(1, cfg.transcript_channel_hard_cooldown_seconds)
    cfg.transcript_breaker_half_open_probe_count = max(1, cfg.transcript_breaker_half_open_probe_count)
    cfg.transcript_worker_lease_ttl_seconds = max(5, cfg.transcript_worker_lease_ttl_seconds)
    cfg.rss_inter_channel_delay_seconds = max(0.0, min(30.0, cfg.rss_inter_channel_delay_seconds))
    cfg.rss_channel_deactivate_after_fails = max(1, min(20, cfg.rss_channel_deactivate_after_fails))
    cfg.rss_consecutive_error_abort_threshold = max(2, min(50, cfg.rss_consecutive_error_abort_threshold))
    cfg.download_max_concurrent = max(1, min(3, cfg.download_max_concurrent))
    cfg.download_timeout_seconds = max(30, cfg.download_timeout_seconds)
    cfg.log_file_max_bytes = max(1024, cfg.log_file_max_bytes)
    cfg.log_file_backup_count = max(1, cfg.log_file_backup_count)
    cfg.log_noise_window_seconds = max(1, cfg.log_noise_window_seconds)
    cfg.log_noise_suppress_threshold = max(1, cfg.log_noise_suppress_threshold)
    cfg.log_dependency_level = cfg.log_dependency_level or "WARNING"
    return cfg
