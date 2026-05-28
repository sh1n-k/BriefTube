from __future__ import annotations

import logging
import time
from pathlib import Path

from app.config import AppConfig, load_config
from app.logging_setup import (
    ColorConsoleFormatter,
    NoiseGateFilter,
    _resolve_console_color_enabled,
    configure_logging,
)


class _TtyStream:
    def __init__(self, *, is_tty: bool) -> None:
        self.is_tty = is_tty

    def isatty(self) -> bool:
        return self.is_tty


def test_configure_logging_creates_rotating_file(tmp_path: Path) -> None:
    cfg = AppConfig(
        log_level="INFO",
        log_to_file=True,
        log_dir=str(tmp_path / "logs"),
        log_file_name="test.log",
        log_file_max_bytes=1024 * 1024,
        log_file_backup_count=2,
    )

    configure_logging(cfg)
    logger = logging.getLogger("tests.logging")
    logger.info("logging-policy-check")

    for handler in logging.getLogger().handlers:
        if hasattr(handler, "flush"):
            handler.flush()

    target = tmp_path / "logs" / "test.log"
    assert target.exists()
    assert "logging-policy-check" in target.read_text(encoding="utf-8")
    assert "\033[" not in target.read_text(encoding="utf-8")


def test_configure_logging_without_file_keeps_console_handler(tmp_path: Path) -> None:
    cfg = AppConfig(
        log_level="INFO",
        log_to_file=False,
        log_dir=str(tmp_path / "logs"),
        log_file_name="test.log",
    )

    configure_logging(cfg)
    handlers = logging.getLogger().handlers
    assert len(handlers) >= 1
    assert not (tmp_path / "logs" / "test.log").exists()


def test_auto_log_level_uses_env_when_log_level_is_auto(tmp_path: Path) -> None:
    dev_cfg = AppConfig(
        env="dev",
        log_level="AUTO",
        log_to_file=False,
        log_dir=str(tmp_path / "logs-dev"),
        log_file_name="dev.log",
    )
    configure_logging(dev_cfg)
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG

    prod_cfg = AppConfig(
        env="prod",
        log_level="AUTO",
        log_to_file=False,
        log_dir=str(tmp_path / "logs-prod"),
        log_file_name="prod.log",
    )
    configure_logging(prod_cfg)
    assert logging.getLogger().getEffectiveLevel() == logging.INFO


def test_explicit_log_level_overrides_env(tmp_path: Path) -> None:
    cfg = AppConfig(
        env="dev",
        log_level="WARNING",
        log_to_file=False,
        log_dir=str(tmp_path / "logs"),
        log_file_name="override.log",
    )
    configure_logging(cfg)
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_dependency_loggers_use_configured_level(tmp_path: Path) -> None:
    cfg = AppConfig(
        log_level="INFO",
        log_to_file=False,
        log_dir=str(tmp_path / "logs"),
        log_file_name="deps.log",
        log_dependency_level="ERROR",
    )
    configure_logging(cfg)
    assert logging.getLogger("httpx").level == logging.ERROR
    assert logging.getLogger("httpcore").level == logging.ERROR
    assert logging.getLogger("aiosqlite").level == logging.ERROR
    assert logging.getLogger("uvicorn.access").level == logging.ERROR


def test_load_config_reads_console_color_from_file_and_env(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text('log_console_color: "never"\n', encoding="utf-8")
    monkeypatch.setenv("APP_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("LOG_CONSOLE_COLOR", raising=False)

    assert load_config().log_console_color == "NEVER"

    monkeypatch.setenv("LOG_CONSOLE_COLOR", "always")
    assert load_config().log_console_color == "ALWAYS"


def test_load_config_reads_rss_fetcher_and_yt_dlp_limits(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                'rss_fetcher_mode: "rss_then_yt_dlp"',
                "yt_dlp_playlist_limit: 12",
                "yt_dlp_timeout_seconds: 45",
                "yt_dlp_longform_min_seconds: 240",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("RSS_FETCHER_MODE", raising=False)

    cfg = load_config()
    assert cfg.rss_fetcher_mode == "rss_then_yt_dlp"
    assert cfg.yt_dlp_playlist_limit == 12
    assert cfg.yt_dlp_timeout_seconds == 45
    assert cfg.yt_dlp_longform_min_seconds == 240

    monkeypatch.setenv("RSS_FETCHER_MODE", "invalid")
    assert load_config().rss_fetcher_mode == "rss"


def test_load_config_falls_back_to_auto_for_invalid_console_color(monkeypatch) -> None:
    monkeypatch.delenv("APP_CONFIG_FILE", raising=False)
    monkeypatch.setenv("LOG_CONSOLE_COLOR", "sometimes")

    assert load_config().log_console_color == "AUTO"


def test_console_color_policy_auto_requires_tty(monkeypatch) -> None:
    cfg = AppConfig(log_console_color="AUTO")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    assert _resolve_console_color_enabled(cfg, _TtyStream(is_tty=True)) is True
    assert _resolve_console_color_enabled(cfg, _TtyStream(is_tty=False)) is False


def test_console_color_policy_respects_no_color_and_dumb_term(monkeypatch) -> None:
    cfg = AppConfig(log_console_color="AUTO")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _resolve_console_color_enabled(cfg, _TtyStream(is_tty=True)) is False

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert _resolve_console_color_enabled(cfg, _TtyStream(is_tty=True)) is False


def test_console_color_policy_supports_always_and_never(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")

    assert _resolve_console_color_enabled(
        AppConfig(log_console_color="ALWAYS"), _TtyStream(is_tty=False)
    )
    assert not _resolve_console_color_enabled(
        AppConfig(log_console_color="NEVER"), _TtyStream(is_tty=True)
    )


def test_color_console_formatter_colors_metadata_only() -> None:
    formatter = ColorConsoleFormatter()
    record = logging.LogRecord(
        name="tests.color",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="event=test.warning",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)

    assert "\033[33mWARNING\033[0m" in formatted
    assert "\033[34mtests.color\033[0m" in formatted
    assert " - event=test.warning" in formatted
    assert record.levelname == "WARNING"


def test_noise_gate_filter_suppresses_repeated_logs_and_emits_summary(monkeypatch) -> None:
    gate = NoiseGateFilter(window_seconds=1, suppress_threshold=1)
    summary_calls: list[tuple[int, str, tuple, dict]] = []

    def _capture_summary(level: int, msg: str, *args, **kwargs) -> None:
        summary_calls.append((level, msg, args, kwargs))

    monkeypatch.setattr(gate._summary_logger, "log", _capture_summary)

    record1 = logging.LogRecord(
        name="tests.noise",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="event=test.repeat worker=rss code=429",
        args=(),
        exc_info=None,
    )
    record2 = logging.LogRecord(
        name="tests.noise",
        level=logging.WARNING,
        pathname=__file__,
        lineno=2,
        msg="event=test.repeat worker=rss code=429",
        args=(),
        exc_info=None,
    )
    record3 = logging.LogRecord(
        name="tests.noise",
        level=logging.WARNING,
        pathname=__file__,
        lineno=3,
        msg="event=test.repeat worker=rss code=429",
        args=(),
        exc_info=None,
    )

    assert gate.filter(record1) is True
    assert gate.filter(record2) is False
    assert gate.filter(record3) is False

    time.sleep(1.05)
    record4 = logging.LogRecord(
        name="tests.noise",
        level=logging.WARNING,
        pathname=__file__,
        lineno=4,
        msg="event=test.repeat worker=rss code=429",
        args=(),
        exc_info=None,
    )
    assert gate.filter(record4) is True
    assert len(summary_calls) == 1
    assert "event=logging.noise_suppressed" in summary_calls[0][1]
    assert summary_calls[0][2][4] == 2
