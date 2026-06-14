from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.llm_capabilities import LlmCapabilityProbe


class _FailingCapabilityProbe(LlmCapabilityProbe):
    async def get_codex_capabilities(self, *, refresh: bool = False) -> Any:
        raise AssertionError("Codex capability probe should not run for this page")


def test_non_llm_pages_do_not_probe_codex_capabilities(client: TestClient) -> None:
    client.app.state.runtime.llm_capability_probe = _FailingCapabilityProbe()

    response = client.get("/")

    assert response.status_code == 200


def test_settings_page_renders(client: TestClient) -> None:
    async def fake_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
        return (
            0,
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.4",
                            "display_name": "GPT-5.4",
                            "supported_reasoning_levels": [
                                {"effort": "low"},
                                {"effort": "medium"},
                                {"effort": "high"},
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

    response = client.get("/settings")
    assert response.status_code == 200
    assert 'href="/settings"' in response.text
    assert 'data-rss-poll-preview="settings"' in response.text
    assert 'hx-put="/api/settings/telegram"' in response.text
    assert 'data-settings-section="remote-sync"' in response.text
    assert 'name="telegram_bot_token"' in response.text
    assert 'name="telegram_chat_id"' in response.text
    assert 'id="llm-runtime-status"' in response.text
    assert 'hx-get="/views/settings/llm/runtime-status"' in response.text
    assert 'hx-post="/views/settings/llm/resume"' in response.text
    assert 'name="llm_provider_primary"' in response.text
    assert 'name="llm_provider_fallback"' in response.text
    assert 'name="llm_max_concurrent"' in response.text
    assert "change from:select[name='llm_max_concurrent']" in response.text
    assert 'name="llm_model_codex"' in response.text
    assert 'value="gpt-5.4"' in response.text
    assert 'name="llm_model_claude"' in response.text
    assert 'name="llm_model_gemini"' in response.text
    assert 'name="llm_reasoning_effort_codex"' in response.text
    assert 'name="llm_reasoning_effort_claude"' in response.text
    assert 'name="llm_reasoning_effort_gemini"' in response.text
    assert 'name="llm_prompt_template"' in response.text
    assert response.text.count('name="llm_prompt_template"') == 1
    assert 'hx-put="/api/settings/llm"' in response.text
    assert response.text.index('name="llm_provider_fallback"') < response.text.index(
        'name="llm_max_concurrent"'
    )
    assert 'value="xhigh"' in response.text
    assert 'name="download_output_dir"' in response.text
    assert "data-download-output-dir-error" in response.text
    assert (
        "change from:select[name='download_quality'], change from:input[name='download_overwrite'], change from:input[name='download_output_dir']"
        in response.text
    )
    assert "hx-trigger=\"change from:select[name='language']\"" in response.text
    assert (
        "hx-trigger=\"input changed delay:1s from:input[name='videos_per_page']\"" in response.text
    )
    assert 'data-save-toast="' in response.text
    assert "data-digits-only" in response.text
    assert 'name="transcript_header_user_agent"' in response.text
    assert 'name="transcript_header_accept"' in response.text
    assert 'name="transcript_header_accept_language"' in response.text
    assert 'name="transcript_header_dnt"' in response.text
    assert 'name="transcript_header_upgrade_insecure_requests"' in response.text
    assert 'name="transcript_request_headers"' not in response.text
    assert 'id="llm-prompt-modal"' in response.text
    assert "data-open-llm-prompt-modal" in response.text
    assert "data-llm-prompt-editor" in response.text
    for section in (
        "workers",
        "language",
        "remote-sync",
        "llm",
        "telegram",
        "transcript-headers",
        "transcript-guard",
    ):
        assert f'data-settings-section="{section}"' in response.text


def test_settings_page_renders_remote_sync_status(client: TestClient) -> None:
    client.app.state.runtime.config.remote_sync_dsn = "sqlite:///remote.db"
    client.app.state.runtime.config.remote_sync_enabled = True
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (
                ("remote_sync_runtime_enabled", "1"),
                ("remote_sync_last_failure_code", "remote_unavailable"),
            ),
        )
        conn.commit()

    response = client.get("/settings")

    assert response.status_code == 200
    assert "Remote sync 상태" in response.text
    assert "configured" in response.text
    assert "requested" in response.text
    assert "active" in response.text
    assert "last_failure_code" in response.text
    assert "remote_unavailable" in response.text
    assert response.text.count(">예<") >= 3


def test_settings_page_preserves_saved_codex_model_outside_probe_options(
    client: TestClient,
) -> None:
    async def fake_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
        return (
            0,
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.4",
                            "display_name": "GPT-5.4",
                            "supported_reasoning_levels": [{"effort": "low"}],
                        }
                    ]
                }
            ),
            "",
        )

    save = client.put(
        "/api/settings/llm",
        json={
            "llm_model": {
                "codex": "gpt-custom-codex",
            }
        },
    )
    assert save.status_code == 200
    client.app.state.runtime.llm_capability_probe = LlmCapabilityProbe(
        command_exists=lambda name: name == "codex",
        runner=fake_runner,
    )

    response = client.get("/settings")

    assert response.status_code == 200
    assert '<option value="gpt-custom-codex" selected>gpt-custom-codex</option>' in response.text


def test_settings_page_rss_poll_preview_uses_active_channel_count(
    client: TestClient,
) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        for idx in range(3):
            channel_id = f"UCpreview{idx:03d}"
            conn.execute(
                """
                INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (
                    channel_id,
                    f"Preview {idx}",
                    f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                ),
            )
        conn.execute(
            """
            INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 0)
            """,
            (
                "UCpreviewinactive",
                "Preview Inactive",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCpreviewinactive",
            ),
        )
        conn.commit()

    response = client.get("/settings")

    assert response.status_code == 200
    assert "300.0초마다 1개 채널 조회" in response.text
    assert "약 210.0초 ~ 390.0초" in response.text
    assert "15분 x 60 / 3개" in response.text


def test_settings_page_masks_stored_telegram_values(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES('telegram_bot_token', '123456:ABCDEFSECRET')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """
        )
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES('telegram_chat_id', '-1001234567890')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """
        )
        conn.commit()

    response = client.get("/settings")
    assert response.status_code == 200
    assert "1234…CRET" in response.text
    assert "-100…7890" in response.text
    assert "123456:ABCDEFSECRET" not in response.text
    assert "-1001234567890" not in response.text


def test_settings_page_ignores_invalid_oversized_telegram_values(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES('telegram_bot_token', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            ("x" * 600,),
        )
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES('telegram_chat_id', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            ("y" * 200,),
        )
        conn.commit()

    response = client.get("/settings")
    assert response.status_code == 200
    assert "현재 미설정" in response.text
    assert ("x" * 50) not in response.text
    assert ("y" * 50) not in response.text


def test_settings_page_guard_cooldown_until_respects_timezone_setting(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES('timezone', 'America/New_York')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """
        )
        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES('transcript_guard_cooldown_until', '2026-02-25T00:00:00+00:00')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """
        )
        conn.commit()

    response = client.get("/settings")
    assert response.status_code == 200
    assert "2026-02-24 19:00" in response.text
    assert "2026-02-25T00:00:00+00:00" not in response.text


def test_settings_llm_resume_view_returns_runtime_fragment_and_toast(client: TestClient) -> None:
    response = client.post("/views/settings/llm/resume")
    assert response.status_code == 200
    assert 'id="llm-runtime-status"' in response.text
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "llm-runtime-toast" in trigger


def test_settings_llm_runtime_status_fragment_renders(client: TestClient) -> None:
    response = client.get("/views/settings/llm/runtime-status")
    assert response.status_code == 200
    assert 'id="llm-runtime-status"' in response.text
    assert 'data-llm-runtime-refresh-url="/views/settings/llm/runtime-status"' in response.text
    assert 'data-llm-runtime-auto-refresh="1"' in response.text


@pytest.mark.parametrize("bulk_text", ["resolved-only"])
def test_bulk_resolve_view_fragment_renders(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    bulk_text: str,
) -> None:
    resolver = client.app.state.runtime.channel_resolver

    async def fake_resolve_input(raw_input: str) -> dict:
        return {
            "input": raw_input,
            "status": "resolved",
            "resolved": {
                "channel_id": "UCview001",
                "channel_name": "View Channel",
                "channel_url": "https://www.youtube.com/channel/UCview001",
            },
        }

    monkeypatch.setattr(resolver, "resolve_input", fake_resolve_input)

    response = client.post(
        "/views/channels/bulk-resolve",
        data={"bulk_text": bulk_text},
    )
    assert response.status_code == 200
    assert "View Channel" in response.text
