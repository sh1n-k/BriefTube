from __future__ import annotations

import json
import os
import sqlite3

from fastapi.testclient import TestClient
import pytest
import re


def test_settings_page_renders(client: TestClient) -> None:
    response = client.get("/settings")
    assert response.status_code == 200
    assert "설정" in response.text
    assert "시간대" in response.text
    assert "테마" in response.text
    assert "목록 설정" in response.text
    assert "수집/보관 정책" in response.text
    assert "워커 제어" in response.text
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
    assert 'name="llm_model_claude"' in response.text
    assert 'name="llm_model_gemini"' in response.text
    assert 'name="llm_reasoning_effort_codex"' in response.text
    assert 'name="llm_reasoning_effort_claude"' in response.text
    assert 'name="llm_reasoning_effort_gemini"' in response.text
    assert 'name="llm_prompt_template"' in response.text
    assert 'hx-put="/api/settings/llm"' in response.text
    assert "Provider별 모델/사고 수준" in response.text
    assert "Codex 모델은 GPT-5.3 Codex 또는 GPT-5.4를 선택할 수 있습니다." in response.text
    assert "Google Gemini CLI" in response.text
    assert "기본 화질 상한" in response.text
    assert "다운로드 저장 경로" in response.text
    assert 'name="download_output_dir"' in response.text
    assert 'data-download-output-dir-error' in response.text
    assert (
        "change from:select[name='download_quality'], change from:input[name='download_overwrite'], change from:input[name='download_output_dir']"
        in response.text
    )
    assert "input changed delay:800ms from:input[name='download_output_dir']" not in response.text
    assert 'hx-trigger="change from:select[name=\'language\']"' in response.text
    assert 'hx-trigger="input changed delay:1s from:input[name=\'videos_per_page\']"' in response.text
    assert 'data-save-toast="' in response.text
    assert 'data-digits-only' in response.text
    assert '/static/js/main-ui.js' in response.text
    assert "window.BRIEFTUBE_UI_BOOTSTRAP" in response.text
    assert 'data-theme-toggle' in response.text
    assert 'data-theme-mode-select' in response.text
    assert 'data-theme-tone-select' in response.text
    assert "brieftube.theme.mode" in response.text
    assert "brieftube.theme.tone" in response.text
    assert "뉴트럴 (기본)" in response.text
    assert "고대비" in response.text
    assert 'document.body.addEventListener("htmx:afterRequest"' not in response.text
    assert "Asia/Tokyo" not in response.text
    assert 'id="channel-list-wrap"' not in response.text
    assert 'href="/settings"' in response.text
    assert response.text.count('href="/settings"') == 1
    assert 'href="/channels"' in response.text
    assert "채널 등록/일괄 추가는 채널 관리 페이지에서 진행합니다." in response.text
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
    assert len(re.findall(r'type="number"', response.text)) >= 3


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
