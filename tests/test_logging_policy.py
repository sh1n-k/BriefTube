from __future__ import annotations

import logging
from pathlib import Path

from app.config import AppConfig
from app.logging_setup import configure_logging


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
