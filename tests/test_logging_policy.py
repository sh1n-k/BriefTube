from __future__ import annotations

import logging
import time
from pathlib import Path

from app.config import AppConfig
from app.logging_setup import NoiseGateFilter, configure_logging


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
