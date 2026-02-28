from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def test_channels_page_uses_korean_by_default(client: TestClient) -> None:
    response = client.get("/channels")
    assert response.status_code == 200
    assert "채널 관리" in response.text
    assert "영상으로 돌아가기" in response.text
    assert "활성 채널" in response.text
    assert "비활성 채널" in response.text


def test_channels_page_switches_to_english(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES('language', 'en')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
        )
        conn.commit()

    response = client.get("/channels")
    assert response.status_code == 200
    assert "Channel Management" in response.text
    assert "Back to Videos" in response.text
    assert "Active" in response.text
    assert "Inactive" in response.text
