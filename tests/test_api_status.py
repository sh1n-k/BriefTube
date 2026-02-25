from __future__ import annotations

from fastapi.testclient import TestClient


def test_queue_status_keys_and_defaults(client: TestClient) -> None:
    response = client.get("/api/status")
    assert response.status_code == 200

    payload = response.json()
    for key in ("pending", "processing", "failed", "manual_review"):
        assert key in payload
        assert isinstance(payload[key], int)

    assert payload["pending"] == 0
    assert payload["processing"] == 0
    assert payload["failed"] == 0
    assert payload["manual_review"] == 0
