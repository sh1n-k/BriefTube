from __future__ import annotations

import os
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient


def test_channel_list_fragment_has_scroll_and_search_controls(client: TestClient) -> None:
    response = client.get("/views/channel-list")
    assert response.status_code == 200
    html = response.text

    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert 'href="/channels?status=active"' in html
    assert 'href="/channels?status=inactive"' in html
    assert "data-channel-search" in html
    assert "data-channel-search-input" in html
    assert "data-channel-search-prev" in html
    assert "data-channel-search-next" in html
    assert "data-channel-manage-form" in html
    assert "data-channel-select-all" in html
    assert 'hx-target="#channel-list-wrap"' in html
    assert 'hx-post="/views/channels/delete-selected"' in html
    assert 'name="status" value="active"' in html
    assert 'data-channel-list-auto-refresh="0"' in html
    assert 'data-channel-list-refresh-url="/views/channel-list?status=active"' in html


def test_channel_list_fragment_renders_rss_poll_preview(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        for idx in range(4):
            channel_id = f"UCpreviewlist{idx:03d}"
            conn.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (
                    channel_id,
                    f"Preview List {idx}",
                    f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                ),
            )
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 0)
            """,
            (
                "UCpreviewlistinactive",
                "Preview List Inactive",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCpreviewlistinactive",
            ),
        )
        conn.commit()

    response = client.get("/views/channel-list")
    assert response.status_code == 200
    html = response.text

    assert 'data-rss-poll-preview="channels"' in html
    assert 'id="rss-poll-preview-wrap" hx-swap-oob="true"' in html
    assert "활성 채널 4개" in html
    assert "비활성 채널 1개" in html
    assert "RSS 평균 225.0초/요청" in html
    assert "약 157.5초 ~ 292.5초" in html


def test_inactive_channel_list_fragment_uses_reactivate_bulk_action(client: TestClient) -> None:
    response = client.get("/views/channel-list?status=inactive")
    assert response.status_code == 200
    html = response.text

    assert 'hx-post="/views/channels/reactivate-selected"' in html
    assert "data-channel-reactivate-bulk-form" in html
    assert 'data-reactivate-batch-limit="' in html
    assert 'data-reactivate-timeout-seconds="' in html
    assert "선택 채널 재활성화" in html
    assert 'name="bulk_action"' in html
    assert 'value="delete"' in html
    assert 'name="status" value="inactive"' in html
    assert 'data-channel-list-auto-refresh="1"' in html
    assert 'data-channel-list-refresh-url="/views/channel-list?status=inactive"' in html


def test_channel_list_move_dropdown_selects_current_category(client: TestClient) -> None:
    created = client.post("/api/categories", json={"name": "선택카테고리"})
    assert created.status_code == 200
    category_id = int(created.json()["id"])

    response = client.get(f"/views/channel-list?status=active&category_id={category_id}")
    assert response.status_code == 200
    html = response.text

    assert re.search(
        rf'<option value="{category_id}"\s+selected>선택카테고리</option>',
        html,
    )


def test_channel_list_renders_meta_compact_and_accordion_controls(client: TestClient) -> None:
    created = client.post(
        "/api/channels",
        json={
            "channel_id": "UCmetaaccordion12345678901",
            "channel_name": "Meta Accordion",
            "channel_handle": "@metaaccordion",
            "channel_thumbnail_url": "https://i.ytimg.com/vi/test/hqdefault.jpg",
            "channel_language_hint": "ko",
        },
    )
    assert created.status_code == 200

    response = client.get("/views/channel-list?status=active")
    assert response.status_code == 200
    html = response.text
    assert "data-channel-meta-root" in html
    assert "data-channel-meta-item" in html
    assert "data-channel-meta-toggle" in html
    assert "data-channel-meta-panel" in html
    assert "data-channel-avatar-img" in html
    assert "@metaaccordion" in html
    assert "업데이트" in html
    assert "자세히" in html


def test_channel_list_displays_decoded_handle_but_keeps_raw_in_db(client: TestClient) -> None:
    raw_handle = "@%ED%95%9C%EA%B8%80"
    created = client.post(
        "/api/channels",
        json={
            "channel_id": "UChandledecode12345678901",
            "channel_name": "Handle Decode",
            "channel_handle": raw_handle,
        },
    )
    assert created.status_code == 200

    response = client.get("/views/channel-list?status=active")
    assert response.status_code == 200
    html = response.text
    assert "@한글" in html

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT channel_handle FROM channels WHERE channel_id = ?",
            ("UChandledecode12345678901",),
        ).fetchone()
    assert row is not None
    assert str(row[0]) == raw_handle


def test_index_poll_button_disables_swap(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'hx-post="/api/poll/trigger"' in html
    assert 'hx-swap="none"' in html


def test_channel_search_enter_shortcuts_support_previous_and_next(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "/static/js/main-ui.js" in html


def test_index_does_not_render_channel_panels(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'hx-post="/api/channels"' not in html
    assert 'id="channel-list-wrap"' not in html


def test_channels_page_renders_add_and_bulk_forms(client: TestClient) -> None:
    response = client.get("/channels")
    assert response.status_code == 200
    html = response.text
    assert "data-channel-compose" in html
    assert "data-channel-compose-toggle" in html
    assert "data-channel-compose-body" in html
    assert 'aria-controls="channel-compose-body"' in html
    assert 'aria-expanded="true"' in html
    assert 'hx-post="/views/channels/add"' in html
    assert "data-channel-compose-form" in html
    assert 'data-submit-busy-label="등록 중..."' in html
    assert 'name="source"' in html
    assert 'hx-post="/views/channels/bulk-resolve"' in html
    assert 'data-submit-busy-label="해석 중..."' in html
    assert 'name="takeout_file"' in html
    assert 'id="rss-poll-preview-wrap"' in html
    assert 'id="rss-poll-preview-wrap" hx-swap-oob="true"' not in html
    assert (
        html.index("data-channel-compose")
        < html.index('id="rss-poll-preview-wrap"')
        < html.index('id="category-sidebar"')
    )


def test_add_channel_view_saves_resolved_channel(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = client.app.state.runtime.channel_resolver

    async def fake_resolve_input(raw_input: str) -> dict:
        return {
            "input": raw_input,
            "status": "resolved",
            "resolved": {
                "channel_id": "UCsingle001",
                "channel_name": "Single Channel",
                "channel_url": "https://www.youtube.com/channel/UCsingle001",
            },
        }

    monkeypatch.setattr(resolver, "resolve_input", fake_resolve_input)

    response = client.post("/views/channels/add", data={"source": "@single"})
    assert response.status_code == 200
    assert "채널이 저장되었습니다." in response.text
    assert 'id="rss-poll-preview-wrap" hx-swap-oob="true"' in response.text
    assert "활성 채널 1개" in response.text
    assert 'id="channel-list-wrap" hx-swap-oob="true"' in response.text
    assert re.search(
        r'<div[^>]*id="category-sidebar"[^>]*hx-swap-oob="true"|<div[^>]*hx-swap-oob="true"[^>]*id="category-sidebar"',
        response.text,
    )

    channels = client.get("/api/channels").json()
    assert any(item["channel_id"] == "UCsingle001" for item in channels)


def test_add_channel_view_preserves_selected_category(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = client.post("/api/categories", json={"name": "저장카테고리"})
    assert created.status_code == 200
    category_id = int(created.json()["id"])
    resolver = client.app.state.runtime.channel_resolver

    async def fake_resolve_input(raw_input: str) -> dict:
        return {
            "input": raw_input,
            "status": "resolved",
            "resolved": {
                "channel_id": "UCcategoryadd001",
                "channel_name": "Category Add",
                "channel_url": "https://www.youtube.com/channel/UCcategoryadd001",
            },
        }

    monkeypatch.setattr(resolver, "resolve_input", fake_resolve_input)

    response = client.post(
        "/views/channels/add",
        data={"source": "@categoryadd", "status": "active", "category_id": str(category_id)},
    )
    assert response.status_code == 200
    assert (
        f'data-channel-list-refresh-url="/views/channel-list?status=active&amp;category_id={category_id}"'
        in response.text
    )

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT category_id FROM channels WHERE channel_id = ?",
            ("UCcategoryadd001",),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == category_id


def test_add_channel_view_requires_selection_when_multiple_candidates(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = client.app.state.runtime.channel_resolver

    async def fake_resolve_input(raw_input: str) -> dict:
        return {
            "input": raw_input,
            "status": "needs_selection",
            "candidates": [
                {
                    "channel_id": "UCcand001",
                    "channel_name": "Candidate One",
                    "channel_url": "https://www.youtube.com/channel/UCcand001",
                },
                {
                    "channel_id": "UCcand002",
                    "channel_name": "Candidate Two",
                    "channel_url": "https://www.youtube.com/channel/UCcand002",
                },
            ],
        }

    monkeypatch.setattr(resolver, "resolve_input", fake_resolve_input)

    response = client.post("/views/channels/add", data={"source": "candidate search"})
    assert response.status_code == 200
    assert 'name="selected_candidate"' in response.text
    assert "선택 채널 저장" in response.text
    assert "Candidate One" in response.text


def test_add_channel_view_saves_selected_candidate(client: TestClient) -> None:
    response = client.post(
        "/views/channels/add",
        data={"source": "candidate search", "selected_candidate": "UCpicked001|||Picked Channel"},
    )
    assert response.status_code == 200
    assert "채널이 저장되었습니다." in response.text
    assert re.search(
        r'<div[^>]*id="category-sidebar"[^>]*hx-swap-oob="true"|<div[^>]*hx-swap-oob="true"[^>]*id="category-sidebar"',
        response.text,
    )

    channels = client.get("/api/channels").json()
    assert any(
        item["channel_id"] == "UCpicked001" and item["channel_name"] == "Picked Channel"
        for item in channels
    )


def test_bulk_commit_refreshes_category_sidebar_oob(client: TestClient) -> None:
    response = client.post(
        "/views/channels/bulk-commit",
        data={
            "status": "active",
            "resolved_channel_id": ["UCbulk001"],
            "resolved_channel_name": ["Bulk Channel One"],
        },
    )
    assert response.status_code == 200
    assert 'id="channel-list-wrap" hx-swap-oob="true"' in response.text
    assert 'id="rss-poll-preview-wrap" hx-swap-oob="true"' in response.text
    assert "활성 채널 1개" in response.text
    assert re.search(
        r'<div[^>]*id="category-sidebar"[^>]*hx-swap-oob="true"|<div[^>]*hx-swap-oob="true"[^>]*id="category-sidebar"',
        response.text,
    )


def test_bulk_resolve_and_commit_preserve_selected_category(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = client.post("/api/categories", json={"name": "일괄카테고리"})
    assert created.status_code == 200
    category_id = int(created.json()["id"])
    resolver = client.app.state.runtime.channel_resolver

    async def fake_resolve_input(raw_input: str) -> dict:
        return {
            "input": raw_input,
            "status": "resolved",
            "resolved": {
                "channel_id": "UCbulkcategory001",
                "channel_name": "Bulk Category",
                "channel_url": "https://www.youtube.com/channel/UCbulkcategory001",
            },
        }

    monkeypatch.setattr(resolver, "resolve_input", fake_resolve_input)

    resolved = client.post(
        "/views/channels/bulk-resolve",
        data={"bulk_text": "@bulkcategory", "status": "active", "category_id": str(category_id)},
    )
    assert resolved.status_code == 200
    assert f'name="category_id" value="{category_id}"' in resolved.text

    committed = client.post(
        "/views/channels/bulk-commit",
        data={
            "status": "active",
            "category_id": str(category_id),
            "resolved_channel_id": ["UCbulkcategory001"],
            "resolved_channel_name": ["Bulk Category"],
        },
    )
    assert committed.status_code == 200
    assert (
        f'data-channel-list-refresh-url="/views/channel-list?status=active&amp;category_id={category_id}"'
        in committed.text
    )

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT category_id FROM channels WHERE channel_id = ?",
            ("UCbulkcategory001",),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == category_id
