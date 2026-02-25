from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _insert_alert(db_path: str, channel_name: str = "문제 채널") -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO system_alerts(alert_type, channel_id, channel_name, message)
            VALUES (?, ?, ?, ?)
            """,
            (
                "rss_channel_not_found",
                "UCalert001",
                channel_name,
                "RSS feed returned 404 Not Found. Channel was deactivated automatically.",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_base_renders_unacknowledged_alert_toast(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    alert_id = _insert_alert(db_path)

    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "RSS 404로 채널이 자동 비활성화되었습니다" in html
    assert "문제 채널" in html
    assert f'hx-post="/views/alerts/{alert_id}/ack"' in html
    assert "내용을 확인했습니다" in html
    assert "fixed bottom-24 right-4" in html
    assert "data-alert-dismiss" in html


def test_alert_ack_requires_checkbox(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    alert_id = _insert_alert(db_path, channel_name="체크 필요 채널")

    response = client.post(f"/views/alerts/{alert_id}/ack", data={})
    assert response.status_code == 400

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT acknowledged_at FROM system_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    assert row is not None
    assert row[0] is None


def test_alert_acknowledge_marks_alert(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    alert_id = _insert_alert(db_path, channel_name="확인 완료 채널")

    response = client.post(f"/views/alerts/{alert_id}/ack", data={"confirmed": "on"})
    assert response.status_code == 200

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT acknowledged_at FROM system_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    assert row is not None
    assert row[0] is not None
