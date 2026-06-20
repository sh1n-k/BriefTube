from __future__ import annotations

import os
import re
import sqlite3

from fastapi.testclient import TestClient


def _set_language(language: str) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value, updated_at) VALUES ('language', ?, datetime('now'))",
            (language,),
        )
        conn.commit()


def test_header_navigation_links_render(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    for href in ("/", "/channels", "/settings", "/downloads", "/queue", "/retention"):
        assert f'href="{href}"' in html
    assert re.search(r'<a href="/"\s+data-nav-transition\s+aria-current="page"', html)


def test_home_renders_korean_by_default(client: TestClient) -> None:
    _set_language("ko")

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '<html lang="ko"' in html
    assert "영상 목록" in html
    assert "채널" in html
    assert "설정" in html
    assert "보관" in html


def test_home_renders_english_when_language_setting_is_en(client: TestClient) -> None:
    _set_language("en")

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '<html lang="en"' in html
    assert "Videos" in html
    assert "Channels" in html
    assert "Settings" in html
    assert "Retention" in html
