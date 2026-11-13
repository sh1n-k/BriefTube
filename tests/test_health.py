from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_static_assets_require_revalidation(client: TestClient) -> None:
    response = client.get("/static/css/tailwind.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    etag = response.headers["etag"]

    revalidated = client.get("/static/css/tailwind.css", headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.headers["cache-control"] == "no-cache"
