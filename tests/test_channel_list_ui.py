from __future__ import annotations

from fastapi.testclient import TestClient
import pytest


def test_channel_list_fragment_has_scroll_and_search_controls(client: TestClient) -> None:
    response = client.get('/views/channel-list')
    assert response.status_code == 200
    html = response.text

    assert 'href="/channels?status=active"' in html
    assert 'href="/channels?status=inactive"' in html
    assert 'data-channel-search' in html
    assert 'data-channel-search-input' in html
    assert 'data-channel-search-prev' in html
    assert 'data-channel-search-next' in html
    assert 'max-h-[560px] overflow-y-auto overscroll-contain' in html
    assert 'shrink-0 whitespace-nowrap' in html
    assert 'data-channel-manage-form' in html
    assert 'data-channel-select-all' in html
    assert 'hx-post="/views/channels/delete-selected"' in html
    assert 'name="status" value="active"' in html


def test_inactive_channel_list_fragment_uses_reactivate_bulk_action(client: TestClient) -> None:
    response = client.get('/views/channel-list?status=inactive')
    assert response.status_code == 200
    html = response.text

    assert 'hx-post="/views/channels/reactivate-selected"' in html
    assert '선택 채널 재활성화' in html
    assert 'name="bulk_action"' in html
    assert 'value="delete"' in html
    assert 'name="status" value="inactive"' in html


def test_index_poll_button_disables_swap(client: TestClient) -> None:
    response = client.get('/')
    assert response.status_code == 200
    html = response.text

    assert 'hx-post="/api/poll/trigger"' in html
    assert 'hx-swap="none"' in html


def test_channel_search_enter_shortcuts_support_previous_and_next(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "step(event.shiftKey ? -1 : 1);" in html


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
    assert 'data-channel-compose' in html
    assert 'data-channel-compose-toggle' in html
    assert 'data-channel-compose-body' in html
    assert 'aria-controls="channel-compose-body"' in html
    assert 'aria-expanded="true"' in html
    assert 'hx-post="/views/channels/add"' in html
    assert 'data-channel-compose-form' in html
    assert 'data-submit-busy-label="등록 중..."' in html
    assert 'name="source"' in html
    assert 'hx-post="/views/channels/bulk-resolve"' in html
    assert 'data-submit-busy-label="해석 중..."' in html
    assert 'name="takeout_file"' in html


def test_add_channel_view_saves_resolved_channel(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert 'id="channel-list-wrap" hx-swap-oob="true"' in response.text

    channels = client.get("/api/channels").json()
    assert any(item["channel_id"] == "UCsingle001" for item in channels)


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

    channels = client.get("/api/channels").json()
    assert any(item["channel_id"] == "UCpicked001" and item["channel_name"] == "Picked Channel" for item in channels)
