from __future__ import annotations

import json
import os
import sqlite3

from fastapi.testclient import TestClient
import httpx


def _seed_channel(
    db_path: str,
    channel_id: str,
    channel_name: str,
    *,
    is_active: int,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(
                channel_id,
                channel_name,
                rss_url,
                is_active
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                channel_id,
                channel_name,
                f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                is_active,
            ),
        )
        conn.commit()


def _seed_rss_not_found_alert(
    db_path: str,
    channel_id: str,
    channel_name: str,
    message: str,
    created_at: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO system_alerts(alert_type, channel_id, channel_name, message, created_at)
            VALUES ('rss_channel_not_found', ?, ?, ?, ?)
            """,
            (channel_id, channel_name, message, created_at),
        )
        conn.commit()


def _parse_reactivate_toast(response) -> dict[str, str]:
    raw = response.headers.get("HX-Trigger", "")
    assert raw
    parsed = json.loads(raw)
    payload = parsed.get("channel-reactivate-toast")
    assert isinstance(payload, dict)
    return payload


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://www.youtube.com/feeds/videos.xml")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"status={status_code}",
        request=request,
        response=response,
    )


def test_inactive_tab_renders_reason_and_time(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive001", "Inactive One", is_active=0)
    _seed_rss_not_found_alert(
        db_path,
        "UCinactive001",
        "Inactive One",
        "RSS returned 404",
        "2026-02-27 12:34:56",
    )

    response = client.get("/channels?status=inactive")
    assert response.status_code == 200
    assert "비활성 사유: RSS returned 404" in response.text
    assert "비활성 시각: 2026-02-27 12:34:56" in response.text
    assert "/views/channels/UCinactive001/reactivate?status=inactive" in response.text


def test_inactive_tab_renders_unknown_when_alert_missing(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive002", "Inactive Two", is_active=0)

    response = client.get("/views/channel-list?status=inactive")
    assert response.status_code == 200
    assert response.text.count("알 수 없음") >= 1


def test_reactivate_single_channel_success_after_rss_probe(
    client: TestClient,
    monkeypatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(
        db_path,
        "UCinactive101",
        "Inactive 101",
        is_active=0,
    )

    async def fake_fetch_channel_feed(channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
        assert channel_id == "UCinactive101"
        return [], "etag-101", "mod-101"

    monkeypatch.setattr(
        client.app.state.runtime.rss_service,
        "fetch_channel_feed",
        fake_fetch_channel_feed,
    )

    response = client.post("/views/channels/UCinactive101/reactivate?status=inactive")
    assert response.status_code == 200
    toast = _parse_reactivate_toast(response)
    assert toast["tone"] == "success"
    assert "Inactive 101" in toast["message"]
    assert "재활성화 완료" in toast["message"]

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT is_active
            FROM channels
            WHERE channel_id='UCinactive101'
            """
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 1


def test_reactivate_single_channel_keeps_inactive_on_rss_failure(
    client: TestClient,
    monkeypatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive102", "Inactive 102", is_active=0)

    async def fake_fetch_channel_feed(channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
        raise _http_error(404)

    monkeypatch.setattr(
        client.app.state.runtime.rss_service,
        "fetch_channel_feed",
        fake_fetch_channel_feed,
    )

    response = client.post("/views/channels/UCinactive102/reactivate?status=inactive")
    assert response.status_code == 200
    toast = _parse_reactivate_toast(response)
    assert toast["tone"] == "error"
    assert "Inactive 102" in toast["message"]
    assert "HTTP 404" in toast["message"]

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT is_active FROM channels WHERE channel_id='UCinactive102'"
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 0


def test_reactivate_selected_channels_partial_success_single_toast(
    client: TestClient,
    monkeypatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive201", "Inactive 201", is_active=0)
    _seed_channel(db_path, "UCinactive202", "Inactive 202", is_active=0)
    _seed_channel(db_path, "UCinactive203", "Inactive 203", is_active=0)

    async def fake_fetch_channel_feed(channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
        if channel_id == "UCinactive202":
            raise _http_error(404)
        return [], "etag", "mod"

    monkeypatch.setattr(
        client.app.state.runtime.rss_service,
        "fetch_channel_feed",
        fake_fetch_channel_feed,
    )

    response = client.post(
        "/views/channels/reactivate-selected",
        data={"status": "inactive", "channel_id": ["UCinactive201", "UCinactive202", "UCinactive203"]},
    )
    assert response.status_code == 200
    toast = _parse_reactivate_toast(response)
    assert toast["tone"] == "error"
    assert "성공 2" in toast["message"]
    assert "실패 1" in toast["message"]
    assert "Inactive 202" in toast["message"]

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT channel_id, is_active
            FROM channels
            WHERE channel_id IN ('UCinactive201', 'UCinactive202', 'UCinactive203')
            ORDER BY channel_id
            """
        ).fetchall()
    assert [(row[0], int(row[1])) for row in rows] == [
        ("UCinactive201", 1),
        ("UCinactive202", 0),
        ("UCinactive203", 1),
    ]


def test_reactivate_selected_channels_requires_selection(client: TestClient) -> None:
    response = client.post(
        "/views/channels/reactivate-selected",
        data={"status": "inactive"},
    )
    assert response.status_code == 200
    toast = _parse_reactivate_toast(response)
    assert toast["tone"] == "error"
    assert "선택된 채널이 없습니다." in toast["message"]


def test_reactivate_selected_channels_enforces_batch_limit(
    client: TestClient,
    monkeypatch,
) -> None:
    db_path = os.environ["DB_PATH"]
    channel_ids: list[str] = []
    for idx in range(51):
        channel_id = f"UCinactive5{idx:02d}"
        channel_ids.append(channel_id)
        _seed_channel(db_path, channel_id, f"Inactive {idx}", is_active=0)

    called = False

    async def fake_fetch_channel_feed(channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
        nonlocal called
        called = True
        return [], "etag", "mod"

    monkeypatch.setattr(
        client.app.state.runtime.rss_service,
        "fetch_channel_feed",
        fake_fetch_channel_feed,
    )

    response = client.post(
        "/views/channels/reactivate-selected",
        data={"status": "inactive", "channel_id": channel_ids},
    )
    assert response.status_code == 200
    toast = _parse_reactivate_toast(response)
    assert toast["tone"] == "error"
    assert "51" in toast["message"]
    assert "50" in toast["message"]
    assert called is False

    with sqlite3.connect(db_path) as conn:
        active_count = conn.execute(
            """
            SELECT COUNT(1)
            FROM channels
            WHERE channel_id LIKE 'UCinactive5%' AND is_active = 1
            """
        ).fetchone()[0]
    assert int(active_count) == 0


def test_reactivate_resets_rss_fail_streak(
    client: TestClient,
    monkeypatch,
) -> None:
    """재활성화는 폴러가 사용하는 rss_fail_streak도 0으로 되돌려야 한다.

    그렇지 않으면 streak가 deactivate 임계치(기본 3) 이상으로 남아 있어
    재활성화 직후 단 1회의 404로 즉시 다시 비활성화된다.
    """
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(
                channel_id,
                channel_name,
                rss_url,
                is_active,
                rss_fail_streak
            )
            VALUES (?, ?, ?, 0, 3)
            """,
            (
                "UCstreakreset001",
                "Streak Reset Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCstreakreset001",
            ),
        )
        conn.commit()

    async def fake_fetch_channel_feed(channel_id: str, etag=None, last_modified=None, feed_mode="long_form_only"):
        return [], "etag", "mod"

    monkeypatch.setattr(
        client.app.state.runtime.rss_service,
        "fetch_channel_feed",
        fake_fetch_channel_feed,
    )

    response = client.post("/views/channels/UCstreakreset001/reactivate?status=inactive")
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT is_active, rss_fail_streak
            FROM channels
            WHERE channel_id='UCstreakreset001'
            """
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 1
    assert int(row[1]) == 0, "rss_fail_streak must reset on reactivation"


def test_reactivate_selected_delete_action_removes_channels(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _seed_channel(db_path, "UCinactive301", "Inactive 301", is_active=0)
    _seed_channel(db_path, "UCinactive302", "Inactive 302", is_active=0)

    response = client.post(
        "/views/channels/reactivate-selected",
        data={
            "status": "inactive",
            "bulk_action": "delete",
            "channel_id": ["UCinactive301", "UCinactive302"],
        },
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") is None

    with sqlite3.connect(db_path) as conn:
        remain = conn.execute(
            """
            SELECT COUNT(1)
            FROM channels
            WHERE channel_id IN ('UCinactive301', 'UCinactive302')
            """
        ).fetchone()[0]
    assert int(remain) == 0
