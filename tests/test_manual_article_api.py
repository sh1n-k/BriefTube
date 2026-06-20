from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient


def _seed_video(
    db_path: str,
    *,
    video_id: str,
    pipeline_status: str = "done",
    with_transcript: bool = False,
    with_article: bool = False,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(channel_id, channel_name, rss_url, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (
                "UCmanualapi001",
                "Manual API Channel",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCmanualapi001",
            ),
        )
        conn.execute(
            """
            INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status, retry_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                "UCmanualapi001",
                f"Manual API {video_id}",
                "2026-02-28T00:00:00+00:00",
                pipeline_status,
                0,
            ),
        )
        if with_transcript:
            conn.execute(
                """
                INSERT INTO transcripts(video_id, raw_text, language, source_type)
                VALUES (?, ?, ?, ?)
                """,
                (video_id, f"Transcript {video_id}", "ko", "manual"),
            )
        if with_article:
            conn.execute(
                """
                INSERT INTO articles(video_id, title, lead, body, fact_box, timestamps)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    f"Article {video_id}",
                    "lead",
                    "body",
                    "{}",
                    "[]",
                ),
            )
        conn.commit()


def test_article_request_api_rejects_when_more_than_ten_selected(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]
    video_ids = [f"vid-manual-max-{idx:02d}" for idx in range(11)]
    for video_id in video_ids:
        _seed_video(db_path, video_id=video_id, with_transcript=True)

    response = client.post(
        "/api/videos/article-request",
        json={"video_ids": video_ids},
    )

    assert response.status_code in (400, 422)
    assert "10" in response.text


def test_article_request_api_returns_new_retry_skip_failed_summary(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]

    class _EventProbe:
        def __init__(self) -> None:
            self.called = False

        def set(self) -> None:
            self.called = True

    wake_probe = _EventProbe()
    client.app.state.runtime.manual_article_wake_event = wake_probe

    _seed_video(
        db_path, video_id="vid-manual-new", pipeline_status="archived", with_transcript=True
    )
    _seed_video(
        db_path,
        video_id="vid-manual-retry",
        pipeline_status="transcript_failed",
        with_transcript=True,
    )
    _seed_video(
        db_path,
        video_id="vid-manual-skip",
        pipeline_status="done",
        with_transcript=True,
        with_article=True,
    )

    response = client.post(
        "/api/videos/article-request",
        json={
            "video_ids": [
                "vid-manual-new",
                "vid-manual-retry",
                "vid-manual-skip",
                "vid-manual-missing",
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"ok", "requested_count", "summary", "llm_worker_waiting"}
    assert payload["ok"] is True
    assert payload["requested_count"] == 4
    assert payload["summary"] == {"new": 1, "retry": 1, "skip": 1, "failed": 1}
    assert payload["llm_worker_waiting"] is False
    assert wake_probe.called is True


def test_article_request_api_allows_when_llm_worker_disabled_and_marks_waiting(
    client: TestClient,
) -> None:
    db_path = os.environ["DB_PATH"]

    class _EventProbe:
        def __init__(self) -> None:
            self.called = False

        def set(self) -> None:
            self.called = True

    wake_probe = _EventProbe()
    client.app.state.runtime.manual_article_wake_event = wake_probe

    _seed_video(
        db_path,
        video_id="vid-manual-disabled-001",
        pipeline_status="transcript_failed",
        with_transcript=False,
    )

    settings_response = client.put(
        "/api/settings/workers",
        json={"workers": {"rss": True, "transcript": True, "llm": False, "notifier": True}},
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["workers"]["llm"] is False

    response = client.post(
        "/api/videos/article-request",
        json={"video_ids": ["vid-manual-disabled-001"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"ok", "requested_count", "summary", "llm_worker_waiting"}
    assert payload["ok"] is True
    assert payload["requested_count"] == 1
    assert payload["summary"] == {"new": 0, "retry": 1, "skip": 0, "failed": 0}
    assert payload["llm_worker_waiting"] is True
    assert wake_probe.called is True

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status
            FROM manual_article_jobs
            WHERE video_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            ("vid-manual-disabled-001",),
        ).fetchone()
    assert row is not None
    assert str(row[0]) == "pending"
