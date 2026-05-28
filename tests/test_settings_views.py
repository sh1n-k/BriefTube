from __future__ import annotations

import json
import os
import re
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
    assert "설정" in response.text
    assert "시간대" in response.text
    assert "테마" in response.text
    assert "목록 설정" in response.text
    assert "수집/보관 정책" in response.text
    assert 'data-rss-poll-preview="settings"' in response.text
    assert "요청 간격 미리보기" in response.text
    assert "워커 제어" in response.text
    assert "Telegram 알림" in response.text
    assert "봇 토큰과 채팅 ID를 SQLite에 저장합니다." in response.text
    assert 'hx-put="/api/settings/telegram"' in response.text
    assert 'name="telegram_bot_token"' in response.text
    assert 'name="telegram_chat_id"' in response.text
    assert 'name="telegram_clear_bot_token"' in response.text
    assert 'name="telegram_clear_chat_id"' in response.text
    assert "영상 다운로드" in response.text
    assert "LLM 재구조화" in response.text
    assert "재구조화 프롬프트 템플릿" in response.text
    assert "{transcript_text}는 필수" in response.text
    assert "런타임 상태" in response.text
    assert "다시 점검" in response.text
    assert "인증 완료 후 재개" in response.text
    assert 'id="llm-runtime-status"' in response.text
    assert 'hx-get="/views/settings/llm/runtime-status"' in response.text
    assert 'hx-post="/views/settings/llm/resume"' in response.text
    assert 'name="llm_provider_primary"' in response.text
    assert 'name="llm_provider_fallback"' in response.text
    assert 'name="llm_model_codex"' in response.text
    assert 'value="gpt-5.4"' in response.text
    assert "Codex 모델 새로고침" in response.text
    assert 'name="llm_model_claude"' in response.text
    assert 'name="llm_model_gemini"' in response.text
    assert 'name="llm_reasoning_effort_codex"' in response.text
    assert 'name="llm_reasoning_effort_claude"' in response.text
    assert 'name="llm_reasoning_effort_gemini"' in response.text
    assert 'name="llm_prompt_template"' in response.text
    assert response.text.count('name="llm_prompt_template"') == 1
    assert 'hx-put="/api/settings/llm"' in response.text
    assert "Provider별 모델/사고 수준" in response.text
    assert "Codex 모델 목록은 설치된 Codex CLI에서 확인" in response.text
    assert 'value="xhigh"' in response.text
    assert "Google Gemini CLI" in response.text
    assert "기본 화질 상한" in response.text
    assert "다운로드 저장 경로" in response.text
    assert 'name="download_output_dir"' in response.text
    assert "data-download-output-dir-error" in response.text
    assert (
        "change from:select[name='download_quality'], change from:input[name='download_overwrite'], change from:input[name='download_output_dir']"
        in response.text
    )
    assert "input changed delay:800ms from:input[name='download_output_dir']" not in response.text
    assert "hx-trigger=\"change from:select[name='language']\"" in response.text
    assert (
        "hx-trigger=\"input changed delay:1s from:input[name='videos_per_page']\"" in response.text
    )
    assert 'data-save-toast="' in response.text
    assert "data-digits-only" in response.text
    assert "/static/js/main-ui.js" in response.text
    assert "window.BRIEFTUBE_UI_BOOTSTRAP" in response.text
    assert "data-theme-toggle" in response.text
    assert "data-theme-mode-select" in response.text
    assert "data-theme-tone-select" in response.text
    assert "brieftube.theme.mode" in response.text
    assert "brieftube.theme.tone" in response.text
    assert "뉴트럴 (기본)" in response.text
    assert "고대비" in response.text
    assert 'document.body.addEventListener("htmx:afterRequest"' not in response.text
    assert "Asia/Tokyo" not in response.text
    assert 'id="channel-list-wrap"' not in response.text
    assert 'href="/settings"' in response.text
    assert response.text.count('href="/settings"') == 1
    assert "채널 관리로 이동" not in response.text
    assert "채널 등록/일괄 추가는 채널 관리 페이지에서 진행합니다." not in response.text
    assert "자막 요청 헤더" in response.text
    assert "Firefox(Windows) 한국어 프로필을 기본으로 사용합니다." in response.text
    assert "고정 키별 값 입력 (빈 값 저장 시 기본값 복원)" in response.text
    assert 'name="transcript_header_user_agent"' in response.text
    assert 'name="transcript_header_accept"' in response.text
    assert 'name="transcript_header_accept_language"' in response.text
    assert 'name="transcript_header_dnt"' in response.text
    assert 'name="transcript_header_upgrade_insecure_requests"' in response.text
    assert 'name="transcript_request_headers"' not in response.text
    assert 'placeholder="ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.3"' in response.text
    assert "자막 보호 상태" in response.text
    assert "주의 구역: 자막 보호 상태 초기화" in response.text
    assert "전체 편집" in response.text
    assert "재구조화 프롬프트 전체 편집" in response.text
    assert 'id="llm-prompt-modal"' in response.text
    assert "data-open-llm-prompt-modal" in response.text
    assert "data-llm-prompt-editor" in response.text
    assert response.text.index('data-settings-section="workers"') < response.text.index(
        'data-settings-section="language"'
    )
    assert response.text.index('data-settings-section="llm"') < response.text.index(
        'data-settings-section="telegram"'
    )
    assert response.text.index('data-settings-section="telegram"') < response.text.index(
        'data-settings-section="transcript-headers"'
    )
    assert response.text.index('data-settings-section="transcript-headers"') < response.text.index(
        'data-settings-section="transcript-guard"'
    )
    assert len(re.findall(r'type="number"', response.text)) >= 3


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
