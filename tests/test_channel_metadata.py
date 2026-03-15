from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.database import init_database, open_database
from app import repository


class _NoopWakeEvent:
    def set(self) -> None:
        return


def _insert_channel(
    db_path: str,
    channel_id: str,
    channel_name: str,
    *,
    metadata_status: str,
    metadata_error: str | None = None,
    is_active: int = 1,
    category_id: int | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels(
                channel_id,
                channel_name,
                rss_url,
                is_active,
                category_id,
                metadata_fetch_status,
                metadata_fetch_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                channel_name,
                f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                is_active,
                category_id,
                metadata_status,
                metadata_error,
            ),
        )
        conn.commit()


def _parse_metadata_toast(response) -> dict[str, str]:
    raw = response.headers.get("HX-Trigger", "")
    assert raw
    parsed = json.loads(raw)
    payload = parsed.get("channel-metadata-toast")
    assert isinstance(payload, dict)
    return payload


def test_init_database_adds_channel_metadata_columns_for_legacy_table(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_channels.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                thumbnail_path TEXT
            )
            """
        )
        conn.commit()

    async def _run() -> tuple[set[str], set[str]]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            column_cursor = await db.execute("PRAGMA table_info(channels)")
            columns = {str(row["name"]) for row in await column_cursor.fetchall()}
            index_cursor = await db.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                  AND name IN ('idx_channels_metadata_next_fetch', 'idx_channels_metadata_status')
                """
            )
            indexes = {str(row["name"]) for row in await index_cursor.fetchall()}
            return columns, indexes
        finally:
            await db.close()

    columns, indexes = asyncio.run(_run())
    assert "last_seen_published_at" in columns
    assert "rss_consecutive_404_count" in columns
    assert "rss_404_first_at" in columns
    assert "created_at" in columns
    assert "metadata_fetched_at" in columns
    assert "metadata_fetch_status" in columns
    assert "metadata_fetch_error" in columns
    assert "metadata_retry_count" in columns
    assert "metadata_next_fetch_at" in columns
    assert "metadata_last_http_status" in columns
    assert indexes == {"idx_channels_metadata_next_fetch", "idx_channels_metadata_status"}


def test_add_channel_sets_created_at_on_legacy_db(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_channels_add.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                thumbnail_path TEXT
            )
            """
        )
        conn.commit()

    async def _run() -> tuple[dict[str, object], str | None]:
        db = await open_database(str(db_path))
        try:
            await init_database(db)
            saved = await repository.add_channel(
                db,
                channel_id="UClegacycreatedat0000000001",
                channel_name="Legacy CreatedAt",
            )
            row = await repository.get_channel_by_id(db, "UClegacycreatedat0000000001")
            return saved, None if row is None else str(row.get("created_at") or "")
        finally:
            await db.close()

    saved, created_at = asyncio.run(_run())
    assert str(saved.get("channel_id")) == "UClegacycreatedat0000000001"
    assert created_at is not None
    assert created_at != ""


def test_retry_failed_channel_metadata_endpoint_queues_failed_rows(client: TestClient) -> None:
    client.app.state.runtime.channel_metadata_wake_event = _NoopWakeEvent()
    db_path = client.app.state.runtime.config.db_path
    _insert_channel(db_path, "UCmdfailed001", "Failed One", metadata_status="failed", metadata_error="e1")
    _insert_channel(db_path, "UCmdrate001", "Rate One", metadata_status="rate_limited", metadata_error="e2")
    _insert_channel(db_path, "UCmdok001", "Success One", metadata_status="success", metadata_error=None)

    response = client.post("/views/channels/metadata/retry-failed?status=active")
    assert response.status_code == 200
    toast = _parse_metadata_toast(response)
    assert toast["tone"] == "success"
    assert "2" in str(toast["message"])

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT channel_id, metadata_fetch_status, metadata_fetch_error
            FROM channels
            WHERE channel_id IN ('UCmdfailed001', 'UCmdrate001', 'UCmdok001')
            ORDER BY channel_id
            """
        ).fetchall()
    assert [(row[0], row[1], row[2]) for row in rows] == [
        ("UCmdfailed001", "pending", None),
        ("UCmdok001", "success", None),
        ("UCmdrate001", "pending", None),
    ]


def test_retry_failed_channel_metadata_respects_status_and_category_filters(client: TestClient) -> None:
    client.app.state.runtime.channel_metadata_wake_event = _NoopWakeEvent()
    db_path = client.app.state.runtime.config.db_path
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO categories(name, sort_order, llm_enabled, processing_stage, is_default)
            VALUES ('Retry-Filter', 50, 1, 'full', 0)
            """
        )
        category_id = int(conn.execute("SELECT id FROM categories WHERE name='Retry-Filter'").fetchone()[0])
        conn.commit()

    _insert_channel(
        db_path,
        "UCfilter-active-cat",
        "Active+Category",
        metadata_status="failed",
        is_active=1,
        category_id=category_id,
    )
    _insert_channel(
        db_path,
        "UCfilter-active-othercat",
        "Active+OtherCategory",
        metadata_status="failed",
        is_active=1,
        category_id=None,
    )
    _insert_channel(
        db_path,
        "UCfilter-inactive-cat",
        "Inactive+Category",
        metadata_status="rate_limited",
        is_active=0,
        category_id=category_id,
    )

    response = client.post(
        f"/views/channels/metadata/retry-failed?status=active&category_id={category_id}"
    )
    assert response.status_code == 200
    toast = _parse_metadata_toast(response)
    assert toast["tone"] == "success"
    assert "1" in str(toast["message"])

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT channel_id, metadata_fetch_status
            FROM channels
            WHERE channel_id IN (
                'UCfilter-active-cat',
                'UCfilter-active-othercat',
                'UCfilter-inactive-cat'
            )
            ORDER BY channel_id
            """
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        ("UCfilter-active-cat", "pending"),
        ("UCfilter-active-othercat", "failed"),
        ("UCfilter-inactive-cat", "rate_limited"),
    ]


def test_retry_failed_channel_metadata_endpoint_returns_info_when_no_target(client: TestClient) -> None:
    client.app.state.runtime.channel_metadata_wake_event = _NoopWakeEvent()
    db_path = client.app.state.runtime.config.db_path
    _insert_channel(db_path, "UCmdok002", "Success Two", metadata_status="success", metadata_error=None)

    response = client.post("/views/channels/metadata/retry-failed?status=active")
    assert response.status_code == 200
    toast = _parse_metadata_toast(response)
    assert toast["tone"] == "info"
    assert "없" in str(toast["message"])
