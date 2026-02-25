from __future__ import annotations

from fastapi.testclient import TestClient


def test_settings_language_default_and_update(client: TestClient) -> None:
    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["language"] == "ko"
    assert initial.json()["timezone"] == "Asia/Seoul"
    assert initial.json()["workers"] == {
        "rss": True,
        "transcript": True,
        "llm": True,
        "notifier": True,
    }
    assert initial.json()["policy"] == {
        "rss_bootstrap_lookback_days": 60,
        "retention_days": 180,
    }
    assert initial.json()["videos_per_page"] == 8

    updated = client.put("/api/settings/language", json={"language": "en"})
    assert updated.status_code == 200
    assert updated.json() == {"ok": True, "language": "en"}

    after = client.get("/api/settings")
    assert after.status_code == 200
    assert after.json()["language"] == "en"


def test_settings_language_rejects_invalid_value(client: TestClient) -> None:
    response = client.put("/api/settings/language", json={"language": "jp"})
    assert response.status_code == 400


def test_settings_timezone_update(client: TestClient) -> None:
    response = client.put("/api/settings/timezone", json={"timezone": "America/New_York"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "timezone": "America/New_York"}

    after = client.get("/api/settings")
    assert after.status_code == 200
    assert after.json()["timezone"] == "America/New_York"


def test_settings_timezone_rejects_invalid_value(client: TestClient) -> None:
    response = client.put("/api/settings/timezone", json={"timezone": "Asia/Invalid"})
    assert response.status_code == 400


def test_settings_videos_per_page_update(client: TestClient) -> None:
    response = client.put("/api/settings/videos-per-page", json={"videos_per_page": 12})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "videos_per_page": 12}

    after = client.get("/api/settings")
    assert after.status_code == 200
    assert after.json()["videos_per_page"] == 12


def test_settings_videos_per_page_rejects_invalid_value(client: TestClient) -> None:
    response = client.put("/api/settings/videos-per-page", json={"videos_per_page": "invalid"})
    assert response.status_code == 400


def test_settings_workers_update(client: TestClient) -> None:
    response = client.put(
        "/api/settings/workers",
        json={"workers": {"rss": False, "transcript": False, "llm": True, "notifier": False}},
    )
    assert response.status_code == 200
    assert response.json()["workers"] == {
        "rss": False,
        "transcript": False,
        "llm": True,
        "notifier": False,
    }

    after = client.get("/api/settings")
    assert after.status_code == 200
    assert after.json()["workers"] == {
        "rss": False,
        "transcript": False,
        "llm": True,
        "notifier": False,
    }

    poll = client.post("/api/poll/trigger")
    assert poll.status_code == 200
    assert poll.json() == {"ok": True, "triggered": False, "reason": "rss_worker_disabled"}


def test_settings_policy_update(client: TestClient) -> None:
    response = client.put(
        "/api/settings/policy",
        json={"rss_bootstrap_lookback_days": 45, "retention_days": 120},
    )
    assert response.status_code == 200
    assert response.json()["policy"] == {
        "rss_bootstrap_lookback_days": 45,
        "retention_days": 120,
    }

    after = client.get("/api/settings")
    assert after.status_code == 200
    assert after.json()["policy"] == {
        "rss_bootstrap_lookback_days": 45,
        "retention_days": 120,
    }
