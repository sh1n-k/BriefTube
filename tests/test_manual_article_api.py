from __future__ import annotations

import json
import os
import sqlite3

from fastapi.testclient import TestClient

from tests.helpers.db_seed import seed_video_job_sync

# Seed key ("<status>" or "<status>:<activity>") → video_jobs row reproducing it in
# video_pipeline_v.
_STATUS_JOBS: dict[str, tuple[str, str, str | None]] = {
    "transcript_failed": ("transcript", "failed", "non_retryable_failure"),
    "no_subtitle": ("transcript", "failed", "no_subtitle"),
    "article_pending": ("article", "queued", None),
    "article_pending:retry_wait": ("article", "queued", "llm_provider_timeout"),
    "article_failed": ("article", "failed", "retry_exhausted"),
}


def _seed_video(
    db_path: str,
    *,
    video_id: str,
    pipeline_status: str = "article_ready",
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
            INSERT INTO videos(video_id, channel_id, title, upload_time)
            VALUES (?, ?, ?, ?)
            """,
            (
                video_id,
                "UCmanualapi001",
                f"Manual API {video_id}",
                "2026-02-28T00:00:00+00:00",
            ),
        )
        if pipeline_status in _STATUS_JOBS:
            kind, status, error_code = _STATUS_JOBS[pipeline_status]
            seed_video_job_sync(conn, video_id, kind, status=status, error_code=error_code)
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


def test_article_request_view_returns_new_retry_skip_failed_summary(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]

    class _EventProbe:
        def __init__(self) -> None:
            self.called = False

        def set(self) -> None:
            self.called = True

    wake_probe = _EventProbe()
    client.app.state.runtime.transcript_wake_event = wake_probe

    # Transcript collected, article never attempted.
    _seed_video(
        db_path, video_id="vid-manual-new", pipeline_status="transcript_ready", with_transcript=True
    )
    _seed_video(
        db_path,
        video_id="vid-manual-retry",
        pipeline_status="article_failed",
        with_transcript=True,
    )
    _seed_video(
        db_path,
        video_id="vid-manual-skip",
        pipeline_status="article_ready",
        with_transcript=True,
        with_article=True,
    )

    response = client.post(
        "/views/videos/article-request-selected",
        data={
            "video_id": [
                "vid-manual-new",
                "vid-manual-retry",
                "vid-manual-skip",
                "vid-manual-missing",
            ],
            "_page": "1",
            "_limit": "20",
        },
    )

    assert response.status_code == 200
    toast = json.loads(response.headers["HX-Trigger"])["video-article-request-toast"]
    message = str(toast.get("message") or "")
    assert "신규 1건, 재시도 1건, 건너뜀 1건, 실패 1건" in message
    assert "LLM 워커가 꺼져 있어" not in message
    assert wake_probe.called is True


def test_article_request_view_allows_when_llm_worker_disabled_and_marks_waiting(
    client: TestClient,
) -> None:
    db_path = os.environ["DB_PATH"]

    class _EventProbe:
        def __init__(self) -> None:
            self.called = False

        def set(self) -> None:
            self.called = True

    wake_probe = _EventProbe()
    client.app.state.runtime.transcript_wake_event = wake_probe

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
        "/views/videos/article-request-selected",
        data={"video_id": ["vid-manual-disabled-001"], "_page": "1", "_limit": "20"},
    )

    assert response.status_code == 200
    toast = json.loads(response.headers["HX-Trigger"])["video-article-request-toast"]
    message = str(toast.get("message") or "")
    assert "신규 0건, 재시도 1건, 건너뜀 0건, 실패 0건" in message
    assert "LLM 워커가 꺼져 있어 대기열에만 등록되었습니다." in message
    assert wake_probe.called is True

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT j.kind, j.status, j.origin, v.target_stage
            FROM video_jobs j
            JOIN videos v ON v.video_id = j.video_id
            WHERE j.video_id = ?
            ORDER BY j.id DESC
            LIMIT 1
            """,
            ("vid-manual-disabled-001",),
        ).fetchone()
    assert row is not None
    # No transcript yet: the request queues a manual transcript job toward the article.
    assert tuple(row) == ("transcript", "queued", "manual", "article")
