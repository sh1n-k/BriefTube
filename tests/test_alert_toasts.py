from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _insert_alert(
    db_path: str,
    *,
    alert_type: str = "rss_channel_not_found",
    channel_id: str = "UCalert001",
    channel_name: str = "문제 채널",
    message: str = "RSS feed returned 404 Not Found. Channel was deactivated automatically.",
) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO system_alerts(alert_type, channel_id, channel_name, message)
            VALUES (?, ?, ?, ?)
            """,
            (
                alert_type,
                channel_id,
                channel_name,
                message,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_base_renders_grouped_alert_toast(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _insert_alert(db_path, channel_id="UCalert001", channel_name="문제 채널 A")
    _insert_alert(db_path, channel_id="UCalert002", channel_name="문제 채널 B")

    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert html.count("RSS 404로 채널이 자동 비활성화되었습니다") == 1
    assert "문제 채널 A" in html
    assert "문제 채널 B" in html
    assert 'hx-post="/views/alerts/ack-group"' in html
    assert 'name="alert_type" value="rss_channel_not_found"' in html
    assert "포함된 채널 목록 보기" in html
    assert "전체 확인 (2)" in html
    assert "내용을 확인했습니다" in html
    assert "fixed bottom-24 right-4" in html
    assert "data-alert-dismiss" in html


def test_base_renders_llm_schema_invalid_alert_text(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _insert_alert(
        db_path,
        alert_type="llm_schema_invalid",
        channel_id="UCallert991",
        channel_name="LLM 스키마 채널",
        message="LLM output schema is incompatible.",
    )

    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "LLM 출력 스키마가 호환되지 않아 기사 재구성이 중지되었습니다" in html


def test_alert_group_ack_requires_checkbox(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    alert_id = _insert_alert(db_path, channel_name="체크 필요 채널")

    response = client.post("/views/alerts/ack-group", data={"alert_type": "rss_channel_not_found"})
    assert response.status_code == 400

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT acknowledged_at FROM system_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    assert row is not None
    assert row[0] is None


def test_alert_group_acknowledge_marks_all_same_type(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    rss_first = _insert_alert(db_path, channel_id="UCalert011", channel_name="RSS 채널 1")
    rss_second = _insert_alert(db_path, channel_id="UCalert012", channel_name="RSS 채널 2")
    llm_alert = _insert_alert(
        db_path,
        alert_type="llm_config_missing",
        channel_id="UCallm001",
        channel_name="LLM 채널",
        message="LLM configuration missing.",
    )

    before = client.get("/")
    assert before.status_code == 200
    assert "RSS 404로 채널이 자동 비활성화되었습니다" in before.text

    response = client.post(
        "/views/alerts/ack-group",
        data={"alert_type": "rss_channel_not_found", "confirmed": "on"},
    )
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        rss_rows = conn.execute(
            "SELECT id, acknowledged_at FROM system_alerts WHERE id IN (?, ?) ORDER BY id",
            (rss_first, rss_second),
        ).fetchall()
        llm_row = conn.execute(
            "SELECT acknowledged_at FROM system_alerts WHERE id = ?",
            (llm_alert,),
        ).fetchone()
    assert len(rss_rows) == 2
    assert all(row[1] is not None for row in rss_rows)
    assert llm_row is not None
    assert llm_row[0] is None

    after = client.get("/")
    assert after.status_code == 200
    assert "RSS 404로 채널이 자동 비활성화되었습니다" not in after.text


def test_alert_group_acknowledge_returns_404_when_already_acknowledged(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    _insert_alert(db_path, channel_name="재확인 채널")

    first = client.post(
        "/views/alerts/ack-group",
        data={"alert_type": "rss_channel_not_found", "confirmed": "on"},
    )
    assert first.status_code == 200

    second = client.post(
        "/views/alerts/ack-group",
        data={"alert_type": "rss_channel_not_found", "confirmed": "on"},
    )
    assert second.status_code == 404


def test_alert_acknowledge_marks_single_alert_via_legacy_endpoint(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    alert_id = _insert_alert(db_path, channel_name="기존 경로 채널")

    before = client.get("/")
    assert before.status_code == 200
    assert "RSS 404로 채널이 자동 비활성화되었습니다" in before.text

    response = client.post(f"/views/alerts/{alert_id}/ack", data={"confirmed": "on"})
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT acknowledged_at FROM system_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    assert row is not None
    assert row[0] is not None

    after = client.get("/")
    assert after.status_code == 200
    assert "RSS 404로 채널이 자동 비활성화되었습니다" not in after.text
