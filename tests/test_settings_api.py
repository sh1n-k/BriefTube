from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.services.llm import LlmRuntimePlan
from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS,
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    TRANSCRIPT_REQUEST_HEADER_PROFILE,
    default_transcript_request_headers,
)


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
        "rss_feed_mode": "long_form_only",
    }
    assert initial.json()["llm_settings"] == {
        "provider_primary": "codex",
        "provider_fallback": "claude",
        "prompt_template": "",
    }
    assert initial.json()["videos_per_page"] == 8
    assert initial.json()["transcript_request_headers"]["profile"] == TRANSCRIPT_REQUEST_HEADER_PROFILE
    assert initial.json()["transcript_request_headers"]["keys"] == list(TRANSCRIPT_REQUEST_HEADER_KEYS)
    assert initial.json()["transcript_request_headers"]["field_names"] == TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS
    assert initial.json()["transcript_request_headers"]["defaults"] == default_transcript_request_headers()
    assert (
        initial.json()["transcript_request_headers"]["values"]["Accept-Language"]
        == default_transcript_request_headers()["Accept-Language"]
    )

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
        "rss_feed_mode": "long_form_only",
    }

    after = client.get("/api/settings")
    assert after.status_code == 200
    assert after.json()["policy"] == {
        "rss_bootstrap_lookback_days": 45,
        "retention_days": 120,
        "rss_feed_mode": "long_form_only",
    }


def test_settings_feed_mode_update(client: TestClient) -> None:
    resp = client.put("/api/settings/policy", json={"rss_feed_mode": "all"})
    assert resp.status_code == 200
    assert resp.json()["policy"]["rss_feed_mode"] == "all"

    after = client.get("/api/settings")
    assert after.json()["policy"]["rss_feed_mode"] == "all"


def test_settings_feed_mode_invalid_fallback(client: TestClient) -> None:
    resp = client.put("/api/settings/policy", json={"rss_feed_mode": "invalid"})
    assert resp.status_code == 200
    assert resp.json()["policy"]["rss_feed_mode"] == "long_form_only"


def test_settings_transcript_request_headers_update_applies_immediately(client: TestClient) -> None:
    defaults = default_transcript_request_headers()
    response = client.put(
        "/api/settings/transcript-request-headers",
        json={
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["User-Agent"]: "Mozilla/5.0 CustomTest",
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["Accept"]: defaults["Accept"],
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["Accept-Language"]: "ko-KR,ko;q=1.0",
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["DNT"]: "",
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["Upgrade-Insecure-Requests"]: defaults[
                "Upgrade-Insecure-Requests"
            ],
        },
    )
    assert response.status_code == 200
    payload = response.json()["transcript_request_headers"]
    assert payload["profile"] == TRANSCRIPT_REQUEST_HEADER_PROFILE
    assert payload["values"]["User-Agent"] == "Mozilla/5.0 CustomTest"
    assert payload["values"]["Accept-Language"] == "ko-KR,ko;q=1.0"
    assert payload["values"]["DNT"] == default_transcript_request_headers()["DNT"]

    after = client.get("/api/settings")
    assert after.status_code == 200
    after_payload = after.json()["transcript_request_headers"]
    assert after_payload["values"]["User-Agent"] == "Mozilla/5.0 CustomTest"
    assert after_payload["values"]["Accept-Language"] == "ko-KR,ko;q=1.0"
    runtime_headers = client.app.state.runtime.transcript_service.get_transcript_request_headers()
    assert runtime_headers["User-Agent"] == "Mozilla/5.0 CustomTest"
    assert runtime_headers["Accept-Language"] == "ko-KR,ko;q=1.0"


def test_settings_transcript_request_headers_legacy_multiline_update(client: TestClient) -> None:
    response = client.put(
        "/api/settings/transcript-request-headers",
        json={"headers_text": "User-Agent: Mozilla/5.0 LegacyPath"},
    )
    assert response.status_code == 200
    payload = response.json()["transcript_request_headers"]
    assert payload["values"]["User-Agent"] == "Mozilla/5.0 LegacyPath"


def test_settings_transcript_request_headers_rejects_unknown_key(client: TestClient) -> None:
    response = client.put(
        "/api/settings/transcript-request-headers",
        json={"headers_text": "X-Test: hello"},
    )
    assert response.status_code == 400


def test_settings_transcript_request_headers_rejects_partial_field_payload(client: TestClient) -> None:
    response = client.put(
        "/api/settings/transcript-request-headers",
        json={
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["User-Agent"]: "Mozilla/5.0 PartialOnly",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "header fields input requires all fixed keys"


def test_settings_transcript_request_headers_rejects_mixed_modes(client: TestClient) -> None:
    response = client.put(
        "/api/settings/transcript-request-headers",
        json={
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["User-Agent"]: "Mozilla/5.0 Mixed",
            "headers_text": "Accept: text/plain",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "mixed header input modes are not allowed"


def test_settings_transcript_request_headers_rejects_empty_payload(client: TestClient) -> None:
    response = client.put(
        "/api/settings/transcript-request-headers",
        json={},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "empty header payload is not allowed"


def test_settings_llm_update(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "claude",
            "provider_fallback": "none",
            "prompt_template": "Title={source_title}\\nBody={transcript_text}",
        },
    )
    assert response.status_code == 200
    assert response.json()["llm_settings"]["provider_primary"] == "claude"
    assert response.json()["llm_settings"]["provider_fallback"] == "none"
    assert response.json()["llm_settings"]["prompt_template"] == "Title={source_title}\\nBody={transcript_text}"

    after = client.get("/api/settings")
    assert after.status_code == 200
    assert after.json()["llm_settings"]["provider_primary"] == "claude"
    assert after.json()["llm_settings"]["provider_fallback"] == "none"


def test_settings_llm_rejects_same_primary_and_fallback(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "codex",
            "provider_fallback": "codex",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "provider_fallback must be different from provider_primary"


def test_settings_llm_rejects_empty_payload(client: TestClient) -> None:
    response = client.put("/api/settings/llm", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "empty llm settings payload"


def test_settings_llm_rejects_prompt_without_transcript_placeholder(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "codex",
            "provider_fallback": "none",
            "prompt_template": "Title={source_title}",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "prompt_template must include {transcript_text}"


def test_settings_llm_rejects_non_object_json_payload(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        json=["invalid"],
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "llm payload must be object"


def test_settings_llm_runtime_status_reports_prompt_missing(client: TestClient) -> None:
    response = client.get("/api/settings/llm/runtime-status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert payload["code"] == "llm_prompt_missing"
    assert payload["pending_count"] == 0


def test_settings_llm_runtime_status_prefers_auth_issue_when_pending(
    client: TestClient,
    monkeypatch,
) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "codex",
            "provider_fallback": "none",
            "prompt_template": "Body={transcript_text}",
        },
    )
    assert response.status_code == 200

    from app.routers import api as api_router

    monkeypatch.setattr(
        client.app.state.runtime.llm_client,
        "resolve_runtime_plan",
        lambda _settings: LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        ),
    )

    async def fake_issue(_db):
        return {
            "code": "llm_provider_auth_required",
            "message": "Not logged in",
            "seen_at": "2026-03-01T00:00:00+00:00",
        }

    async def fake_pending(_db):
        return 2

    monkeypatch.setattr(api_router.llm_repo, "get_llm_runtime_issue", fake_issue)
    monkeypatch.setattr(api_router.llm_repo, "count_llm_pending_videos", fake_pending)

    runtime_response = client.get("/api/settings/llm/runtime-status")
    assert runtime_response.status_code == 200
    payload = runtime_response.json()
    assert payload["ready"] is False
    assert payload["code"] == "llm_provider_auth_required"
    assert payload["pending_count"] == 2


def test_settings_llm_resume_returns_409_when_runtime_not_ready(client: TestClient) -> None:
    response = client.post("/api/settings/llm/resume")
    assert response.status_code == 409
    assert response.json()["ok"] is False
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "llm-runtime-toast" in trigger


def test_settings_llm_resume_wakes_worker_when_ready(
    client: TestClient,
    monkeypatch,
) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "codex",
            "provider_fallback": "none",
            "prompt_template": "Body={transcript_text}",
        },
    )
    assert response.status_code == 200

    from app.routers import api as api_router

    monkeypatch.setattr(
        client.app.state.runtime.llm_client,
        "resolve_runtime_plan",
        lambda _settings: LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        ),
    )

    async def fake_issue(_db):
        return {"code": "", "message": "", "seen_at": ""}

    async def fake_pending(_db):
        return 3

    monkeypatch.setattr(api_router.llm_repo, "get_llm_runtime_issue", fake_issue)
    monkeypatch.setattr(api_router.llm_repo, "count_llm_pending_videos", fake_pending)

    client.app.state.runtime.llm_wake_event.clear()
    resume_response = client.post("/api/settings/llm/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["ok"] is True
    assert resume_response.json()["resumed_count"] == 3
    assert client.app.state.runtime.llm_wake_event.is_set() is True
    trigger = json.loads(resume_response.headers.get("HX-Trigger", "{}"))
    assert "llm-runtime-toast" in trigger


def test_settings_llm_resume_allows_retry_when_auth_issue_exists(
    client: TestClient,
    monkeypatch,
) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "codex",
            "provider_fallback": "none",
            "prompt_template": "Body={transcript_text}",
        },
    )
    assert response.status_code == 200

    from app.routers import api as api_router

    monkeypatch.setattr(
        client.app.state.runtime.llm_client,
        "resolve_runtime_plan",
        lambda _settings: LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        ),
    )

    async def fake_issue(_db):
        return {
            "code": "llm_provider_auth_required",
            "message": "Not logged in",
            "seen_at": "2026-03-01T00:00:00+00:00",
        }

    async def fake_pending(_db):
        return 2

    monkeypatch.setattr(api_router.llm_repo, "get_llm_runtime_issue", fake_issue)
    monkeypatch.setattr(api_router.llm_repo, "count_llm_pending_videos", fake_pending)

    client.app.state.runtime.llm_wake_event.clear()
    resume_response = client.post("/api/settings/llm/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["ok"] is True
    assert resume_response.json()["resumed_count"] == 2
    assert client.app.state.runtime.llm_wake_event.is_set() is True
