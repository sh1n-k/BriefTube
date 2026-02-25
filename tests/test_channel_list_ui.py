from __future__ import annotations

from fastapi.testclient import TestClient


def test_channel_list_fragment_has_scroll_and_search_controls(client: TestClient) -> None:
    response = client.get('/views/channel-list')
    assert response.status_code == 200
    html = response.text

    assert 'data-channel-search' in html
    assert 'data-channel-search-input' in html
    assert 'data-channel-search-prev' in html
    assert 'data-channel-search-next' in html
    assert 'max-h-[560px] overflow-y-auto overscroll-contain' in html
    assert 'shrink-0 whitespace-nowrap' in html
    assert 'data-channel-manage-form' in html
    assert 'data-channel-select-all' in html


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
