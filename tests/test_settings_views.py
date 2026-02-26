from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
import re


def test_settings_page_renders(client: TestClient) -> None:
    response = client.get("/settings")
    assert response.status_code == 200
    assert "설정" in response.text
    assert "시간대" in response.text
    assert "목록 설정" in response.text
    assert "수집/보관 정책" in response.text
    assert "워커 제어" in response.text
    assert 'hx-trigger="change from:select[name=\'language\']"' in response.text
    assert 'hx-trigger="input changed delay:1s from:input[name=\'videos_per_page\']"' in response.text
    assert 'data-save-toast="' in response.text
    assert 'data-digits-only' in response.text
    assert 'document.addEventListener("htmx:afterRequest"' in response.text
    assert 'flattenSavedValues' in response.text
    assert 'parseJsonSafe' in response.text
    assert 'document.body.addEventListener("htmx:afterRequest"' not in response.text
    assert "Asia/Tokyo" not in response.text
    assert 'id="channel-list-wrap"' not in response.text
    assert 'href="/settings"' in response.text
    assert response.text.count('href="/settings"') == 1
    assert 'href="/channels"' in response.text
    assert "채널 등록/일괄 추가는 채널 관리 페이지에서 진행합니다." in response.text
    assert "자막 보호 상태" in response.text
    assert "주의 구역: 자막 보호 상태 초기화" in response.text
    assert len(re.findall(r'type="number"', response.text)) >= 3


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
