from __future__ import annotations

import logging
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import AppConfig

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
DEV_ENV_NAMES = {"dev", "local", "development"}
DEPENDENCY_LOGGERS = (
    "httpx",
    "httpcore",
    "aiosqlite",
    "uvicorn.access",
    "python_multipart",
    "python_multipart.multipart",
)


class NoiseGateFilter(logging.Filter):
    def __init__(self, window_seconds: int, suppress_threshold: int):
        super().__init__()
        self.window_seconds = max(1, int(window_seconds))
        self.suppress_threshold = max(1, int(suppress_threshold))
        self._state: dict[tuple[str, int, str, str, str], dict[str, float | int]] = {}
        self._lock = threading.Lock()
        self._summary_logger = logging.getLogger("app.logging.noise_gate")

    def _make_key(self, record: logging.LogRecord) -> tuple[str, int, str, str, str]:
        event = str(getattr(record, "event", "") or "")
        category = str(getattr(record, "category", "") or "")
        code = str(getattr(record, "code", "") or "")
        message_template = str(record.msg)
        return (record.name, record.levelno, event, category, code or message_template)

    def _emit_summary(self, key: tuple[str, int, str, str, str], suppressed: int) -> None:
        logger_name, levelno, event, category, code_or_message = key
        self._summary_logger.log(
            levelno,
            "event=logging.noise_suppressed logger=%s event_key=%s category=%s key=%s suppressed=%s window_seconds=%s",
            logger_name,
            event or "-",
            category or "-",
            code_or_message,
            suppressed,
            self.window_seconds,
            extra={"noise_gate_summary": True},
        )

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "noise_gate_summary", False):
            return True
        if record.levelno < logging.WARNING:
            return True

        now = time.monotonic()
        key = self._make_key(record)

        with self._lock:
            slot = self._state.get(key)
            if slot is None:
                self._state[key] = {"window_start": now, "count": 1, "suppressed": 0}
                return True

            elapsed = now - float(slot["window_start"])
            if elapsed >= self.window_seconds:
                suppressed = int(slot["suppressed"])
                self._state[key] = {"window_start": now, "count": 1, "suppressed": 0}
                if suppressed > 0:
                    self._emit_summary(key, suppressed)
                return True

            slot["count"] = int(slot["count"]) + 1
            if int(slot["count"]) <= self.suppress_threshold:
                return True
            slot["suppressed"] = int(slot["suppressed"]) + 1
            return False


def _resolve_effective_log_level(config: AppConfig) -> int:
    configured = (config.log_level or "").strip().upper()
    if configured and configured != "AUTO":
        return getattr(logging, configured, logging.INFO)
    env_name = (config.env or "").strip().lower()
    if env_name in DEV_ENV_NAMES:
        return logging.DEBUG
    return logging.INFO


def _resolve_dependency_level(config: AppConfig) -> int:
    level_name = (config.log_dependency_level or "WARNING").strip().upper()
    return getattr(logging, level_name, logging.WARNING)


def configure_logging(config: AppConfig) -> None:
    level = _resolve_effective_log_level(config)
    dependency_level = _resolve_dependency_level(config)

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(DEFAULT_FORMAT)
    noise_filter = NoiseGateFilter(
        window_seconds=config.log_noise_window_seconds,
        suppress_threshold=config.log_noise_suppress_threshold,
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(noise_filter)
    root.addHandler(console_handler)

    if config.log_to_file:
        try:
            log_dir = Path(config.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)

            file_handler = RotatingFileHandler(
                filename=log_dir / config.log_file_name,
                maxBytes=max(1024, int(config.log_file_max_bytes)),
                backupCount=max(1, int(config.log_file_backup_count)),
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(noise_filter)
            root.addHandler(file_handler)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "event=logging.file_handler_init_failed log_dir=%s file_name=%s error=%s",
                config.log_dir,
                config.log_file_name,
                exc,
                extra={"event": "logging.file_handler_init_failed"},
            )

    for logger_name in DEPENDENCY_LOGGERS:
        logging.getLogger(logger_name).setLevel(dependency_level)
