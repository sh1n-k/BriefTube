from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_channel_json_and_list(client: TestClient) -> None:
    response = client.post(
        "/api/channels",
        json={
            "channel_id": "UCjson001",
            "channel_name": "JSON Channel",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["channel_id"] == "UCjson001"
    assert payload["channel_name"] == "JSON Channel"
    assert payload["is_active"] == 1

    list_response = client.get("/api/channels")
    assert list_response.status_code == 200
    channels = list_response.json()
    assert any(channel["channel_id"] == "UCjson001" for channel in channels)


def test_create_channel_form_and_list(client: TestClient) -> None:
    response = client.post(
        "/api/channels",
        data={
            "channel_id": "UCform001",
            "channel_name": "Form Channel",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["channel_id"] == "UCform001"
    assert payload["channel_name"] == "Form Channel"
    assert payload["is_active"] == 1

    list_response = client.get("/api/channels")
    assert list_response.status_code == 200
    channels = list_response.json()
    assert any(channel["channel_id"] == "UCform001" for channel in channels)
