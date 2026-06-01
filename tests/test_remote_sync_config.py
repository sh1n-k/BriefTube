from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import load_config


def test_remote_sync_disabled_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("BRIEFTUBE_REMOTE_SYNC_DSN", raising=False)
    monkeypatch.delenv("BRIEFTUBE_REMOTE_SYNC_ENABLED", raising=False)

    cfg = load_config()

    assert cfg.remote_sync_enabled is False
    assert cfg.remote_sync_dsn == ""
    assert cfg.remote_sync_push_interval_seconds == 300
    assert cfg.remote_sync_batch_size == 100
    assert cfg.remote_sync_tombstone_retention_days == 30


def test_remote_sync_dsn_is_environment_only(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "remote_sync_dsn: postgresql://from-file",
                "remote_sync_enabled: true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("BRIEFTUBE_REMOTE_SYNC_DSN", raising=False)
    monkeypatch.delenv("BRIEFTUBE_REMOTE_SYNC_ENABLED", raising=False)

    cfg = load_config()

    assert cfg.remote_sync_dsn == ""
    assert cfg.remote_sync_enabled is False


def test_remote_sync_status_is_local_mode_when_unconfigured(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200

    payload = response.json()
    assert payload["remote_sync"]["configured"] is False
    assert payload["remote_sync"]["enabled"] is False


def test_remote_sync_unconfigured_keeps_delete_hard_delete(client: TestClient) -> None:
    db_path = client.app.state.runtime.config.db_path
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES ('UC-sync-delete', 'Sync Delete', 'https://example.test/rss', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES ('vid-sync-delete', 'UC-sync-delete', 'Video', '2026-02-25T00:00:00+00:00', 'done')
            """
        )
        conn.commit()

    response = client.post("/views/channels/UC-sync-delete/delete")
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute("SELECT 1 FROM channels WHERE channel_id = 'UC-sync-delete'").fetchone()
            is None
        )
        assert (
            conn.execute("SELECT 1 FROM videos WHERE video_id = 'vid-sync-delete'").fetchone()
            is None
        )


def test_remote_sync_unreachable_startup_keeps_local_delete_behavior(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "unreachable.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbnails"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("BRIEFTUBE_REMOTE_SYNC_DSN", "postgresql://127.0.0.1:1/db")
    monkeypatch.setenv("BRIEFTUBE_REMOTE_SYNC_CONNECT_TIMEOUT_SECONDS", "1")

    from app.main import app

    with TestClient(app) as test_client:
        with sqlite3.connect(db_path) as conn:
            runtime_enabled = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'remote_sync_runtime_enabled'"
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES ('UC-sync-unreachable', 'Sync Unreachable', 'https://example.test/rss', 1)
                """
            )
            conn.commit()

        response = test_client.post("/views/channels/UC-sync-unreachable/delete")
        assert response.status_code == 200

    assert runtime_enabled == "0"
    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM channels WHERE channel_id = 'UC-sync-unreachable'"
            ).fetchone()
            is None
        )
