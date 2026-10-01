from __future__ import annotations

import json
import os
import sqlite3
from functools import partial

import pytest
from fastapi.testclient import TestClient

from app.i18n import get_texts
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.routers.api_settings_transcript_headers import build_transcript_header_payload
from app.services.llm import LlmRuntimePlan
from app.services.llm_capabilities import LlmCapabilityProbe, resolve_codex_capabilities
from app.services.telegram import build_telegram_settings_payload
from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS,
    TRANSCRIPT_REQUEST_HEADER_PROFILE,
    default_transcript_request_headers,
)
from app.timezone_policy import DEFAULT_TIMEZONE
from tests.helpers.app_repo import call_repo


def _telegram_settings_payload(client: TestClient) -> dict[str, object]:
    stored = call_repo(client, settings_repo.get_telegram_settings)
    return build_telegram_settings_payload(
        client.app.state.runtime.config,
        stored_bot_token=stored["bot_token"],
        stored_chat_id=stored["chat_id"],
    )


def test_settings_language_default_and_update(client: TestClient) -> None:
    assert call_repo(client, settings_repo.get_setting, key="language", default="") == "ko"
    assert call_repo(client, settings_repo.get_setting, key="timezone", default="") == ""
    assert DEFAULT_TIMEZONE == "Asia/Seoul"
    assert call_repo(client, settings_repo.get_worker_settings) == {
        "rss": True,
        "transcript": True,
        "llm": True,
        "notifier": True,
    }
    assert call_repo(client, settings_repo.get_policy_settings)["rss_feed_mode"] == "long_form_only"
    llm_settings = call_repo(client, settings_repo.get_llm_settings)
    assert llm_settings["provider_primary"] == "codex"
    assert llm_settings["provider_fallback"] == "none"
    assert llm_settings["max_concurrent"] == 1
    telegram_settings = _telegram_settings_payload(client)
    assert telegram_settings["configured"] is False
    assert telegram_settings["bot_token_source"] == "none"
    assert call_repo(client, settings_repo.get_videos_per_page_setting) == 8
    header_overrides = call_repo(client, transcripts_repo.get_transcript_request_header_overrides)
    assert (
        build_transcript_header_payload(header_overrides)["profile"]
        == TRANSCRIPT_REQUEST_HEADER_PROFILE
    )

    updated = client.put("/api/settings/language", json={"language": "en"})
    assert updated.status_code == 200
    assert updated.json() == {"ok": True, "language": "en"}

    assert call_repo(client, settings_repo.get_setting, key="language", default="ko") == "en"


def test_settings_language_rejects_invalid_value(client: TestClient) -> None:
    response = client.put("/api/settings/language", json={"language": "jp"})
    assert response.status_code == 400


def test_settings_timezone_update(client: TestClient) -> None:
    response = client.put("/api/settings/timezone", json={"timezone": "America/New_York"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "timezone": "America/New_York"}

    assert (
        call_repo(client, settings_repo.get_setting, key="timezone", default="Asia/Seoul")
        == "America/New_York"
    )


def test_settings_timezone_rejects_invalid_value(client: TestClient) -> None:
    response = client.put("/api/settings/timezone", json={"timezone": "Asia/Invalid"})
    assert response.status_code == 400


def test_settings_videos_per_page_update(client: TestClient) -> None:
    response = client.put("/api/settings/videos-per-page", json={"videos_per_page": 12})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "videos_per_page": 12}

    assert call_repo(client, settings_repo.get_videos_per_page_setting) == 12


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

    assert call_repo(client, settings_repo.get_worker_settings) == {
        "rss": False,
        "transcript": False,
        "llm": True,
        "notifier": False,
    }

    poll = client.post("/api/poll/trigger")
    assert poll.status_code == 200
    assert poll.json() == {"ok": True, "triggered": False, "reason": "rss_worker_disabled"}


def test_settings_workers_rejects_invalid_json_shape(client: TestClient) -> None:
    assert client.put("/api/settings/workers", json=[]).status_code == 400
    assert client.put("/api/settings/workers", json={"workers": []}).status_code == 400


def test_settings_telegram_update_masks_values_and_configures_runtime(client: TestClient) -> None:
    response = client.put(
        "/api/settings/telegram",
        json={
            "bot_token": "123456:ABCDEFSECRET",
            "chat_id": "-1001234567890",
        },
    )
    assert response.status_code == 200
    payload = response.json()["telegram_settings"]
    assert payload["configured"] is True
    assert payload["bot_token_stored"] is True
    assert payload["chat_id_stored"] is True
    assert payload["stored_bot_token_preview"] == "1234…CRET"
    assert payload["stored_chat_id_preview"] == "-100…7890"
    assert payload["effective_bot_token_preview"] == "1234…CRET"
    assert payload["effective_chat_id_preview"] == "-100…7890"
    assert payload["bot_token_source"] == "db"
    assert payload["chat_id_source"] == "db"
    assert "ABCDEFSECRET" not in json.dumps(response.json(), ensure_ascii=False)

    after = _telegram_settings_payload(client)
    assert after["configured"] is True
    assert "ABCDEFSECRET" not in json.dumps(after, ensure_ascii=False)
    assert client.app.state.runtime.telegram_notifier.is_configured() is True
    assert client.app.state.runtime.telegram_notifier.chat_id == "-1001234567890"
    assert "123456:ABCDEFSECRET" in client.app.state.runtime.telegram_notifier.url


def test_settings_telegram_clear_removes_stored_values(client: TestClient) -> None:
    seed = client.put(
        "/api/settings/telegram",
        json={"bot_token": "123456:ABCDEFSECRET", "chat_id": "-1001234567890"},
    )
    assert seed.status_code == 200

    response = client.put(
        "/api/settings/telegram",
        json={"clear_bot_token": True, "clear_chat_id": True},
    )
    assert response.status_code == 200
    payload = response.json()["telegram_settings"]
    assert payload["configured"] is False
    assert payload["bot_token_stored"] is False
    assert payload["chat_id_stored"] is False
    assert payload["bot_token_source"] == "none"
    assert payload["chat_id_source"] == "none"
    assert client.app.state.runtime.telegram_notifier.is_configured() is False


def test_settings_telegram_env_override_takes_priority(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token-1234")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat-5678")

    response = client.put(
        "/api/settings/telegram",
        json={"bot_token": "db-token-9999", "chat_id": "db-chat-9999"},
    )
    assert response.status_code == 200
    payload = response.json()["telegram_settings"]
    assert payload["configured"] is True
    assert payload["override_active"] is True
    assert payload["bot_token_source"] == "env"
    assert payload["chat_id_source"] == "env"
    assert payload["effective_bot_token_preview"] == "env-…1234"
    assert payload["effective_chat_id_preview"] == "env-…5678"
    assert payload["stored_bot_token_preview"] == "db-t…9999"
    assert payload["stored_chat_id_preview"] == "db-c…9999"
    assert client.app.state.runtime.telegram_notifier.chat_id == "env-chat-5678"
    assert "env-token-1234" in client.app.state.runtime.telegram_notifier.url


def test_settings_telegram_partial_env_does_not_mix_with_db(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = client.put(
        "/api/settings/telegram",
        json={"bot_token": "db-token-9999", "chat_id": "db-chat-9999"},
    )
    assert seed.status_code == 200

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token-only")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    payload = _telegram_settings_payload(client)
    assert payload["configured"] is True
    assert payload["bot_token_source"] == "db"
    assert payload["chat_id_source"] == "db"
    assert payload["effective_bot_token_preview"] == "db-t…9999"
    assert payload["effective_chat_id_preview"] == "db-c…9999"


def test_settings_telegram_rejects_empty_payload(client: TestClient) -> None:
    response = client.put("/api/settings/telegram", json={})
    assert response.status_code == 400


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

    assert call_repo(client, settings_repo.get_policy_settings) == {
        "rss_bootstrap_lookback_days": 45,
        "retention_days": 120,
        "rss_feed_mode": "long_form_only",
    }


def test_settings_policy_rejects_invalid_json_shape(client: TestClient) -> None:
    assert client.put("/api/settings/policy", json=[]).status_code == 400


def test_settings_feed_mode_update(client: TestClient) -> None:
    resp = client.put("/api/settings/policy", json={"rss_feed_mode": "all"})
    assert resp.status_code == 200
    assert resp.json()["policy"]["rss_feed_mode"] == "all"

    assert call_repo(client, settings_repo.get_policy_settings)["rss_feed_mode"] == "all"


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

    after_payload = build_transcript_header_payload(
        call_repo(client, transcripts_repo.get_transcript_request_header_overrides)
    )
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


def test_settings_transcript_request_headers_rejects_partial_field_payload(
    client: TestClient,
) -> None:
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
            "provider_primary": "codex",
            "provider_fallback": "none",
            "prompt_template": "Title={source_title}\\nBody={transcript_text}",
            "llm_model": {
                "codex": "gpt-5.4",
            },
            "llm_reasoning_effort": {
                "codex": "high",
            },
            "max_concurrent": 4,
        },
    )
    assert response.status_code == 200
    assert response.json()["llm_settings"]["provider_primary"] == "codex"
    assert response.json()["llm_settings"]["provider_fallback"] == "none"
    assert (
        response.json()["llm_settings"]["prompt_template"]
        == "Title={source_title}\\nBody={transcript_text}"
    )
    assert response.json()["llm_settings"]["llm_model"]["codex"] == "gpt-5.4"
    assert response.json()["llm_settings"]["llm_model"]["grok"] == "grok-4.5"
    assert response.json()["llm_settings"]["llm_reasoning_effort"]["codex"] == "high"
    assert response.json()["llm_settings"]["llm_reasoning_effort"]["grok"] == ""
    assert response.json()["llm_settings"]["max_concurrent"] == 4

    after_llm_settings = call_repo(client, settings_repo.get_llm_settings)
    assert after_llm_settings["provider_primary"] == "codex"
    assert after_llm_settings["provider_fallback"] == "none"
    assert after_llm_settings["llm_model"]["codex"] == "gpt-5.4"
    assert after_llm_settings["llm_model"]["grok"] == "grok-4.5"
    assert after_llm_settings["llm_reasoning_effort"]["codex"] == "high"
    assert after_llm_settings["llm_reasoning_effort"]["grok"] == ""
    assert after_llm_settings["max_concurrent"] == 4


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"max_concurrent": 5}, "max_concurrent must be between 1 and 4"),
        ({"provider_primary": "claude"}, "provider_primary must be one of: codex, cursor, grok"),
        ({"provider_fallback": "claude"}, "fallback provider is not supported"),
        ({}, "empty llm settings payload"),
        (
            {
                "provider_primary": "codex",
                "provider_fallback": "none",
                "prompt_template": "Title={source_title}",
            },
            "prompt_template must include {transcript_text}",
        ),
        (["invalid"], "llm payload must be object"),
        ({"llm_model": "invalid"}, "llm_model must be object"),
        (
            {"llm_model": {"codex": "x" * 201}},
            "llm_model.codex is too long (max 200)",
        ),
        (
            {"llm_model": {"claude": "sonnet"}},
            "llm_model must contain only codex, cursor, grok",
        ),
        (
            {"llm_reasoning_effort": {"gemini": "low"}},
            "llm_reasoning_effort must contain only codex, grok",
        ),
        (
            {"llm_reasoning_effort": {"cursor": "high"}},
            "llm_reasoning_effort must contain only codex, grok",
        ),
        (
            {"llm_reasoning_effort": {"codex": "ultra"}},
            "reasoning_effort must be one of: high, low, medium, xhigh",
        ),
    ],
)
def test_settings_llm_rejects_invalid_payloads(
    client: TestClient,
    payload: object,
    detail: str,
) -> None:
    response = client.put(
        "/api/settings/llm",
        json=payload,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_settings_llm_accepts_custom_codex_model(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "llm_model": {
                "codex": "custom-codex-model",
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["llm_settings"]["llm_model"]["codex"] == "custom-codex-model"


def test_settings_llm_accepts_codex_xhigh_reasoning_effort(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "llm_reasoning_effort": {
                "codex": "xhigh",
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["llm_settings"]["llm_reasoning_effort"]["codex"] == "xhigh"


def test_settings_llm_rejects_grok_xhigh_reasoning_effort(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "llm_reasoning_effort": {
                "grok": "xhigh",
            }
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "reasoning_effort must be one of: high, low, medium"


def test_settings_llm_capabilities_reports_codex_models(client: TestClient) -> None:
    calls = 0

    async def fake_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
        nonlocal calls
        calls += 1
        return (
            0,
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-test-codex",
                            "display_name": "GPT Test Codex",
                            "supported_reasoning_levels": [
                                {"effort": "low"},
                                {"effort": "xhigh"},
                            ],
                        }
                    ]
                }
            ),
            "",
        )

    client.app.state.runtime.llm_capability_probe = LlmCapabilityProbe(
        command_exists=lambda name: name == "codex",
        runner=fake_runner,
    )

    runtime = client.app.state.runtime
    capabilities = client.portal.call(resolve_codex_capabilities, runtime).as_payload()
    refreshed = client.portal.call(
        partial(resolve_codex_capabilities, runtime, refresh=True)
    ).as_payload()

    assert capabilities["models"] == [
        {
            "value": "gpt-test-codex",
            "label": "GPT Test Codex",
            "default_reasoning_effort": "",
            "reasoning_efforts": ["low", "xhigh"],
        }
    ]
    assert refreshed["reasoning_efforts"] == ["low", "xhigh"]
    assert calls == 2


def test_settings_llm_partial_model_update_preserves_reasoning_effort(
    client: TestClient,
) -> None:
    first = client.put(
        "/api/settings/llm",
        json={
            "llm_reasoning_effort": {
                "codex": "high",
            }
        },
    )
    assert first.status_code == 200

    second = client.put(
        "/api/settings/llm",
        json={
            "llm_model": {
                "codex": "gpt-5.4",
            }
        },
    )
    assert second.status_code == 200
    assert second.json()["llm_settings"]["llm_reasoning_effort"]["codex"] == "high"
    assert second.json()["llm_settings"]["llm_reasoning_effort"]["grok"] == ""


def test_settings_llm_form_update_with_model_and_effort(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        data={
            "llm_provider_primary": "codex",
            "llm_provider_fallback": "none",
            "llm_prompt_template": "Body={transcript_text}",
            "llm_model_codex": "gpt-5.4",
            "llm_model_grok": "grok-4.6",
            "llm_reasoning_effort_codex": "medium",
            "llm_max_concurrent": "3",
        },
    )
    assert response.status_code == 200
    llm_settings = response.json()["llm_settings"]
    assert llm_settings["llm_model"]["codex"] == "gpt-5.4"
    assert llm_settings["llm_model"]["grok"] == "grok-4.6"
    assert llm_settings["llm_reasoning_effort"]["codex"] == "medium"
    assert llm_settings["llm_reasoning_effort"]["grok"] == ""
    assert llm_settings["max_concurrent"] == 3


def test_settings_llm_update_accepts_grok_provider_model_and_effort(
    client: TestClient,
) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "grok",
            "provider_fallback": "none",
            "prompt_template": "Body={transcript_text}",
            "llm_model": {"grok": "grok-4.6"},
            "llm_reasoning_effort": {"grok": "high"},
        },
    )
    assert response.status_code == 200
    llm_settings = response.json()["llm_settings"]
    assert llm_settings["provider_primary"] == "grok"
    assert llm_settings["llm_model"]["grok"] == "grok-4.6"
    assert llm_settings["llm_reasoning_effort"]["grok"] == "high"

    after_llm_settings = call_repo(client, settings_repo.get_llm_settings)
    assert after_llm_settings["provider_primary"] == "grok"
    assert after_llm_settings["llm_model"]["grok"] == "grok-4.6"
    assert after_llm_settings["llm_reasoning_effort"]["grok"] == "high"


def test_settings_llm_update_accepts_cursor_provider_and_model(
    client: TestClient,
) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "cursor",
            "provider_fallback": "none",
            "prompt_template": "Body={transcript_text}",
            "llm_model": {"cursor": "grok-4.7-high"},
        },
    )
    assert response.status_code == 200
    llm_settings = response.json()["llm_settings"]
    assert llm_settings["provider_primary"] == "cursor"
    assert llm_settings["llm_model"]["cursor"] == "grok-4.7-high"
    assert "cursor" not in llm_settings["llm_reasoning_effort"]

    after_llm_settings = call_repo(client, settings_repo.get_llm_settings)
    assert after_llm_settings["provider_primary"] == "cursor"
    assert after_llm_settings["llm_model"]["cursor"] == "grok-4.7-high"


def test_settings_llm_form_update_accepts_cursor_model(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        data={"llm_provider_primary": "cursor", "llm_model_cursor": "grok-4.7-high"},
    )
    assert response.status_code == 200
    llm_settings = response.json()["llm_settings"]
    assert llm_settings["provider_primary"] == "cursor"
    assert llm_settings["llm_model"]["cursor"] == "grok-4.7-high"


def test_settings_llm_update_blocks_when_schema_preflight_fails(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        client.app.state.runtime.llm_client,
        "resolve_runtime_plan",
        lambda _settings: LlmRuntimePlan(
            providers_to_try=[],
            blocking_reason="llm_provider_schema_invalid_codex",
            warnings=[],
        ),
    )

    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "codex",
            "provider_fallback": "none",
            "prompt_template": "Body={transcript_text}",
            "llm_model": {
                "codex": "gpt-5.4",
            },
            "llm_reasoning_effort": {
                "codex": "high",
            },
        },
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "llm_provider_schema_invalid_codex"
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "llm-runtime-toast" in trigger

    after_llm_settings = call_repo(client, settings_repo.get_llm_settings)
    assert after_llm_settings["provider_primary"] == "codex"
    assert after_llm_settings["provider_fallback"] == "none"
    assert after_llm_settings["max_concurrent"] == 1


def test_settings_llm_update_rejects_unsupported_provider(client: TestClient) -> None:
    response = client.put(
        "/api/settings/llm",
        json={
            "provider_primary": "gemini",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "provider_primary must be one of: codex, cursor, grok"

    after_llm_settings = call_repo(client, settings_repo.get_llm_settings)
    assert after_llm_settings["provider_primary"] == "codex"
    assert after_llm_settings["provider_fallback"] == "none"


FRAGMENT_HEADERS = {"HX-Request": "true"}
TXT = get_texts("ko")


def _enable_codex_runtime(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    issue: dict[str, str] | None = None,
    pending: int | None = None,
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
    monkeypatch.setattr(
        client.app.state.runtime.llm_client,
        "resolve_runtime_plan",
        lambda _settings: LlmRuntimePlan(
            providers_to_try=["codex"],
            blocking_reason=None,
            warnings=[],
        ),
    )
    if issue is not None:

        async def fake_issue(_db):
            return issue

        monkeypatch.setattr(llm_repo, "get_llm_runtime_issue", fake_issue)
    if pending is not None:

        async def fake_pending(_db):
            return pending

        monkeypatch.setattr(llm_repo, "count_llm_pending_videos", fake_pending)


def _runtime_toast(response) -> dict[str, str]:
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    return trigger["llm-runtime-toast"]


def test_settings_llm_runtime_status_reports_prompt_missing(client: TestClient) -> None:
    response = client.get("/views/settings/llm/runtime-status", headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    assert TXT["settings_llm_runtime_not_ready"] in response.text
    assert TXT["settings_llm_runtime_reason_prompt_missing"] in response.text
    assert f"{TXT['settings_llm_runtime_pending_label']}: 0" in response.text


def test_settings_llm_runtime_status_prefers_auth_issue_when_pending(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_codex_runtime(
        client,
        monkeypatch,
        issue={
            "code": "llm_provider_auth_required",
            "message": "Not logged in",
            "seen_at": "2026-03-01T00:00:00+00:00",
        },
        pending=2,
    )

    response = client.get("/views/settings/llm/runtime-status", headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    assert TXT["settings_llm_runtime_not_ready"] in response.text
    assert TXT["settings_llm_runtime_reason_auth_required"] in response.text
    assert f"{TXT['settings_llm_runtime_pending_label']}: 2" in response.text


def test_settings_llm_runtime_status_ignores_stale_unavailable_issue_when_ready(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_codex_runtime(
        client,
        monkeypatch,
        issue={
            "code": "llm_provider_unavailable_codex",
            "message": "codex command not found",
            "seen_at": "2026-03-01T00:00:00+00:00",
        },
        pending=2,
    )

    response = client.get("/views/settings/llm/runtime-status", headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    assert TXT["settings_llm_runtime_ready"] in response.text
    assert "border-emerald-200" in response.text
    assert (
        f"{TXT['settings_llm_runtime_status_label']}: {TXT['settings_llm_runtime_reason_ok']}"
        in response.text
    )
    assert TXT["settings_llm_runtime_reason_generic"] not in response.text
    assert f"{TXT['settings_llm_runtime_pending_label']}: 2" in response.text


@pytest.mark.parametrize(
    "blocking_reason",
    [
        "llm_prompt_missing",
        "llm_provider_unavailable_codex",
        "llm_provider_schema_invalid_codex",
    ],
)
def test_settings_llm_resume_is_blocked_when_runtime_is_blocked(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    blocking_reason: str,
) -> None:
    if blocking_reason != "llm_prompt_missing":
        monkeypatch.setattr(
            client.app.state.runtime.llm_client,
            "resolve_runtime_plan",
            lambda _settings: LlmRuntimePlan(
                providers_to_try=[],
                blocking_reason=blocking_reason,
                warnings=[],
            ),
        )
    client.app.state.runtime.llm_wake_event.clear()
    response = client.post("/views/settings/llm/resume")
    assert response.status_code == 200
    assert 'id="llm-runtime-status"' in response.text
    assert _runtime_toast(response)["tone"] == "error"
    assert client.app.state.runtime.llm_wake_event.is_set() is False


def test_settings_llm_resume_wakes_worker_when_ready(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_codex_runtime(
        client,
        monkeypatch,
        issue={"code": "", "message": "", "seen_at": ""},
        pending=3,
    )

    client.app.state.runtime.llm_wake_event.clear()
    resume_response = client.post("/views/settings/llm/resume")
    assert resume_response.status_code == 200
    toast = _runtime_toast(resume_response)
    assert toast["tone"] == "success"
    assert toast["message"] == TXT["settings_llm_runtime_resume_requested_toast"].format(count=3)
    assert client.app.state.runtime.llm_wake_event.is_set() is True


def test_settings_llm_resume_wakes_worker_when_stale_unavailable_issue_exists(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_codex_runtime(
        client,
        monkeypatch,
        issue={
            "code": "llm_provider_unavailable_codex",
            "message": "codex command not found",
            "seen_at": "2026-03-01T00:00:00+00:00",
        },
        pending=3,
    )

    client.app.state.runtime.llm_wake_event.clear()
    resume_response = client.post("/views/settings/llm/resume")
    assert resume_response.status_code == 200
    toast = _runtime_toast(resume_response)
    assert toast["tone"] == "success"
    assert toast["message"] == TXT["settings_llm_runtime_resume_requested_toast"].format(count=3)
    assert client.app.state.runtime.llm_wake_event.is_set() is True


def test_settings_llm_resume_allows_retry_when_auth_issue_exists(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_codex_runtime(
        client,
        monkeypatch,
        issue={
            "code": "llm_provider_auth_required",
            "message": "Not logged in",
            "seen_at": "2026-03-01T00:00:00+00:00",
        },
        pending=2,
    )

    client.app.state.runtime.llm_wake_event.clear()
    resume_response = client.post("/views/settings/llm/resume")
    assert resume_response.status_code == 200
    toast = _runtime_toast(resume_response)
    assert toast["tone"] == "success"
    assert toast["message"] == TXT["settings_llm_runtime_resume_requested_toast"].format(count=2)
    assert client.app.state.runtime.llm_wake_event.is_set() is True


def test_settings_llm_resume_clears_auth_issue_for_worker_retry(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_codex_runtime(client, monkeypatch)

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCresume001",
                "Resume Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCresume001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
            VALUES (?, ?, ?, ?, 'llm_pending')
            """,
            ("vid-resume-001", "UCresume001", "resume video", "2026-02-24T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO transcripts(video_id, raw_text, language, source_type)
            VALUES (?, ?, ?, ?)
            """,
            ("vid-resume-001", "hello transcript", "ko", "manual"),
        )
        conn.executemany(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("llm_runtime_last_code", "llm_provider_auth_required"),
                ("llm_runtime_last_message", "Not logged in"),
                ("llm_runtime_last_seen_at", "2026-03-01T00:00:00+00:00"),
            ),
        )
        conn.commit()

    client.app.state.runtime.llm_wake_event.clear()
    resume_response = client.post("/views/settings/llm/resume")
    assert resume_response.status_code == 200
    assert _runtime_toast(resume_response)["tone"] == "success"
    assert client.app.state.runtime.llm_wake_event.is_set() is True
    # The returned fragment is rebuilt after the issue is cleared.
    assert TXT["settings_llm_runtime_ready"] in resume_response.text
    assert TXT["settings_llm_runtime_reason_auth_required"] not in resume_response.text

    with sqlite3.connect(db_path) as conn:
        code = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'llm_runtime_last_code'"
        ).fetchone()[0]
    assert code == ""
