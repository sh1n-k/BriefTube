from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "test.db"
    thumbnail_dir = tmp_path / "thumbnails"
    download_dir = tmp_path / "downloads"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbnail_dir))
    monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.delenv("BRIEFTUBE_REMOTE_SYNC_DSN", raising=False)
    monkeypatch.delenv("BRIEFTUBE_REMOTE_SYNC_ENABLED", raising=False)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
