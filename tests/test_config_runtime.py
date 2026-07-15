from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import load_config


def test_load_config_warns_for_unknown_yaml_key(monkeypatch, tmp_path: Path, caplog) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("unknown_option: true\n", encoding="utf-8")
    monkeypatch.setenv("APP_CONFIG_FILE", str(config_path))

    with caplog.at_level(logging.WARNING, logger="app.config"):
        load_config()

    assert "event=config.unknown_yaml_key" in caplog.text
    assert "key=unknown_option" in caplog.text


def test_load_config_reads_server_settings_from_yaml(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                'server_host: "127.0.0.2"',
                "server_port: 48123",
                "server_reload: true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG_FILE", str(config_path))
    for env_name in ("SERVER_HOST", "HOST", "SERVER_PORT", "PORT", "SERVER_RELOAD"):
        monkeypatch.delenv(env_name, raising=False)

    cfg = load_config()

    assert cfg.server_host == "127.0.0.2"
    assert cfg.server_port == 48123
    assert cfg.server_reload is True


def test_load_config_allows_server_environment_overrides(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                'server_host: "127.0.0.2"',
                "server_port: 48123",
                "server_reload: false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("SERVER_HOST", "127.0.0.9")
    monkeypatch.setenv("SERVER_PORT", "49000")
    monkeypatch.setenv("SERVER_RELOAD", "true")

    cfg = load_config()

    assert cfg.server_host == "127.0.0.9"
    assert cfg.server_port == 49000
    assert cfg.server_reload is True


def test_project_configs_define_server_runtime(monkeypatch) -> None:
    root_dir = Path(__file__).resolve().parents[1]
    for env_name in ("SERVER_HOST", "HOST", "SERVER_PORT", "PORT", "SERVER_RELOAD"):
        monkeypatch.delenv(env_name, raising=False)

    monkeypatch.setenv("APP_CONFIG_FILE", str(root_dir / "config.dev.yaml"))
    dev_cfg = load_config()
    assert dev_cfg.server_host == "127.0.0.1"
    assert dev_cfg.server_port == 48080
    assert dev_cfg.server_reload is True

    monkeypatch.setenv("APP_CONFIG_FILE", str(root_dir / "config.prod.yaml"))
    prod_cfg = load_config()
    assert prod_cfg.server_host == "127.0.0.1"
    assert prod_cfg.server_port == 48080
    assert prod_cfg.server_reload is False


def test_cli_uses_yaml_server_settings(monkeypatch, tmp_path: Path) -> None:
    from app import cli

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                'server_host: "127.0.0.3"',
                "server_port: 48124",
                "server_reload: true",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setenv("APP_CONFIG_FILE", str(config_path))
    for env_name in ("SERVER_HOST", "HOST", "SERVER_PORT", "PORT", "SERVER_RELOAD"):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr("sys.argv", ["brieftube"])
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert captured == {
        "app": "app.main:app",
        "host": "127.0.0.3",
        "port": 48124,
        "reload": True,
    }
