from __future__ import annotations

import json
import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_channel(
    db_path: str,
    channel_id: str,
    channel_name: str,
    *,
    is_active: int,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, ?)
            """,
            (
                channel_id,
                channel_name,
                f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                is_active,
            ),
        )
        conn.commit()


def test_views_channel_list_fragment_contract(client: TestClient) -> None:
    response = client.get("/views/channel-list?status=active")
    assert response.status_code == 200
    html = response.text
    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert "data-channel-manage-form" in html
    assert 'hx-target="#channel-list-wrap"' in html


def test_views_category_sidebar_fragment_contract(client: TestClient) -> None:
    response = client.get("/views/category-sidebar?status=active")
    assert response.status_code == 200
    html = response.text
    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert 'id="category-sidebar"' in html
    assert 'hx-target="#channel-list-wrap"' in html


def test_create_category_fragment_returns_dual_oob_contract(client: TestClient) -> None:
    response = client.post(
        "/views/categories",
        data={"name": "HTMX계약", "status": "active"},
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="channel-list-wrap" hx-swap-oob="true"' in html
    assert 'id="category-sidebar"' in html
    assert 'hx-swap-oob="true"' in html


def test_reactivate_selected_returns_single_toast_trigger_with_fragment(
    client: TestClient,
    monkeypatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UChtmxreact001", "HTMX Reactivate", is_active=0)

    async def fake_fetch_channel_feed(
        channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"
    ):
        assert channel_id == "UChtmxreact001"
        return [], "etag", "last-modified"

    monkeypatch.setattr(
        client.app.state.runtime.rss_service,
        "fetch_channel_feed",
        fake_fetch_channel_feed,
    )

    response = client.post(
        "/views/channels/reactivate-selected",
        data={"status": "inactive", "channel_id": ["UChtmxreact001"]},
    )
    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert "data-channel-manage-form" in response.text

    trigger_raw = response.headers.get("HX-Trigger")
    assert trigger_raw
    trigger_payload = json.loads(trigger_raw)
    assert list(trigger_payload.keys()) == ["channel-reactivate-toast"]
    toast = trigger_payload["channel-reactivate-toast"]
    assert toast["tone"] == "success"
    assert "재활성화" in toast["message"]
