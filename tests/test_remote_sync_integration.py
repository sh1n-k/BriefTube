from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.config import AppConfig
from app.database import init_database, open_database
from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.repositories import llm as llm_repo
from app.repositories import remote_sync as remote_sync_repo
from app.repositories import transcripts as transcripts_repo
from app.repositories import videos as videos_repo
from app.services import remote_sync as remote_sync_service


class InMemoryRemoteSyncGateway:
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    schema_mismatch = False
    prune_calls = 0

    def __init__(self, *, dsn: str, connect_timeout_seconds: int) -> None:
        assert dsn == "postgresql://in-memory.test/db"
        assert connect_timeout_seconds >= 1

    @classmethod
    def reset(cls) -> None:
        cls.rows = {
            "categories": {},
            "channels": {},
            "videos": {},
            "transcripts": {},
            "articles": {},
        }
        cls.schema_mismatch = False
        cls.prune_calls = 0

    async def ensure_schema(self) -> None:
        if self.schema_mismatch:
            raise remote_sync_service.RemoteSyncSchemaMismatch()

    async def fetch_all(self, *, batch_size: int) -> dict[str, list[dict[str, Any]]]:
        return {
            table: sorted(
                (dict(row) for row in rows.values()),
                key=lambda row: str(row.get("updated_at") or ""),
            )
            for table, rows in self.rows.items()
        }

    async def push(self, rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        for table, rows in rows_by_table.items():
            for row in rows:
                key = _remote_key(table, row)
                if not key:
                    continue
                existing = self.rows[table].get(key)
                if existing is None or _is_newer(row, existing):
                    self.rows[table][key] = dict(row)

    async def prune(self, *, retention_days: int, batch_size: int) -> int:
        type(self).prune_calls += 1
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        deleted = 0
        for table in ("articles", "transcripts", "videos", "channels", "categories"):
            for key, row in list(self.rows[table].items())[:batch_size]:
                deleted_at = _parse_dt(row.get("deleted_at"))
                if deleted_at is not None and deleted_at < cutoff:
                    del self.rows[table][key]
                    deleted += 1
        return deleted


def test_remote_sync_two_local_one_remote_flow(monkeypatch, tmp_path: Path) -> None:
    async def _run() -> dict[str, object]:
        InMemoryRemoteSyncGateway.reset()
        monkeypatch.setattr(
            remote_sync_service,
            "RemoteSyncGateway",
            InMemoryRemoteSyncGateway,
        )
        db_a = await open_database(str(tmp_path / "device-a.db"))
        db_b = await open_database(str(tmp_path / "device-b.db"))
        try:
            await init_database(db_a)
            await init_database(db_b)
            config = _sync_config()

            category_a = await categories_repo.create_category(db_a, "Shared")
            await channels_repo.add_channel(
                db_a,
                "UCshared001",
                "Shared Channel",
                category_id=int(category_a["id"]),
            )
            await videos_repo.insert_video_if_absent(
                db_a,
                "vid-shared-001",
                "UCshared001",
                "Shared Video",
                "2026-06-01T00:00:00.000Z",
            )
            await transcripts_repo.save_transcript(
                db_a,
                "vid-shared-001",
                "remote sync transcript",
                "en",
                "manual",
                None,
                force_llm_pending=True,
            )
            await db_a.execute(
                "UPDATE videos SET pipeline_status = 'llm_processing' WHERE video_id = ?",
                ("vid-shared-001",),
            )
            await db_a.commit()
            await llm_repo.save_article(
                db_a,
                "vid-shared-001",
                "Article",
                "Lead",
                "Body",
                None,
                None,
                "codex",
                "test-model",
                "",
                "2026-06-01T00:00:01.000Z",
            )

            await remote_sync_service.run_push_once(config, db_a)
            await remote_sync_service.run_startup_pull(config, db_b)

            pulled = await _read_projection(db_b, "UCshared001", "vid-shared-001")
            assert pulled["category_name"] == "Shared"
            assert pulled["channel_name"] == "Shared Channel"
            assert pulled["video_title"] == "Shared Video"
            assert pulled["transcript_text"] == "remote sync transcript"
            assert pulled["article_title"] == "Article"

            category_id_b = await _category_id_by_uid(
                db_b,
                str(category_a["category_uid"]),
            )
            await categories_repo.rename_category(db_b, category_id_b, "Shared Renamed")
            await remote_sync_service.run_push_once(config, db_b)
            await remote_sync_service.run_startup_pull(config, db_a)
            category_name_a = await _category_name_by_uid(db_a, str(category_a["category_uid"]))
            assert category_name_a == "Shared Renamed"

            await channels_repo.delete_channels_with_related_data(db_b, ["UCshared001"])
            await remote_sync_service.run_push_once(config, db_b)
            await remote_sync_service.run_startup_pull(config, db_a)

            tombstones = await _read_tombstones(db_a, "UCshared001", "vid-shared-001")
            assert tombstones["channel_deleted_at"]
            assert tombstones["video_deleted_at"]
            assert tombstones["transcript_deleted_at"]
            assert tombstones["article_deleted_at"]

            old_deleted_at = "2020-01-01T00:00:00.000Z"
            for table in ("channels", "videos", "transcripts", "articles"):
                for row in InMemoryRemoteSyncGateway.rows[table].values():
                    row["deleted_at"] = old_deleted_at
            await remote_sync_service.run_push_once(config, db_a)
            assert "UCshared001" not in InMemoryRemoteSyncGateway.rows["channels"]
            assert "vid-shared-001" not in InMemoryRemoteSyncGateway.rows["videos"]
            assert InMemoryRemoteSyncGateway.prune_calls >= 1

            return pulled
        finally:
            await db_a.close()
            await db_b.close()

    result = asyncio.run(_run())
    assert result["article_title"] == "Article"


def test_remote_sync_schema_mismatch_disables_sync(monkeypatch, tmp_path: Path) -> None:
    async def _run() -> dict[str, str]:
        InMemoryRemoteSyncGateway.reset()
        InMemoryRemoteSyncGateway.schema_mismatch = True
        monkeypatch.setattr(
            remote_sync_service,
            "RemoteSyncGateway",
            InMemoryRemoteSyncGateway,
        )
        db = await open_database(str(tmp_path / "schema-mismatch.db"))
        try:
            await init_database(db)
            await remote_sync_service.run_startup_pull(_sync_config(), db)
            cursor = await db.execute(
                """
                SELECT key, value
                FROM app_settings
                WHERE key IN ('remote_sync_runtime_enabled', 'remote_sync_last_failure_code',
                              'remote_sync_schema_version_status')
                """
            )
            rows = await cursor.fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}
        finally:
            await db.close()

    status = asyncio.run(_run())
    assert status["remote_sync_runtime_enabled"] == "0"
    assert status["remote_sync_last_failure_code"] == "schema_mismatch"
    assert status["remote_sync_schema_version_status"] == "mismatch"


def test_remote_sync_dirty_batch_limit_is_total(tmp_path: Path) -> None:
    async def _run() -> int:
        db = await open_database(str(tmp_path / "batch.db"))
        try:
            await init_database(db)
            category = await categories_repo.create_category(db, "Batch")
            await channels_repo.add_channel(
                db, "UCbatch001", "Batch", category_id=int(category["id"])
            )
            rows = await remote_sync_repo.list_dirty_rows(db, batch_size=1)
            return sum(len(table_rows) for table_rows in rows.values())
        finally:
            await db.close()

    assert asyncio.run(_run()) == 1


def test_remote_sync_pull_preserves_newer_local_dirty_row(tmp_path: Path) -> None:
    async def _run() -> dict[str, object]:
        db = await open_database(str(tmp_path / "conflict.db"))
        try:
            await init_database(db)
            category = await categories_repo.create_category(db, "Local New")
            await db.execute(
                """
                UPDATE categories
                SET updated_at = '2026-06-01T00:00:02.000Z',
                    sync_dirty = 1,
                    origin_device_id = 'local-device'
                WHERE category_uid = ?
                """,
                (category["category_uid"],),
            )
            await db.commit()
            applied = await remote_sync_repo.apply_remote_rows(
                db,
                {
                    "categories": [
                        {
                            "category_uid": category["category_uid"],
                            "name": "Remote Old",
                            "sort_order": 0,
                            "processing_stage": "off",
                            "is_default": 0,
                            "created_at": "2026-06-01T00:00:00.000Z",
                            "updated_at": "2026-06-01T00:00:01.000Z",
                            "deleted_at": None,
                            "origin_device_id": "remote-device",
                        }
                    ]
                },
            )
            cursor = await db.execute(
                "SELECT name, sync_dirty FROM categories WHERE category_uid = ?",
                (category["category_uid"],),
            )
            row = await cursor.fetchone()
            assert row is not None
            return {
                "applied": applied["categories"],
                "name": row["name"],
                "sync_dirty": row["sync_dirty"],
            }
        finally:
            await db.close()

    result = asyncio.run(_run())

    assert result == {"applied": 0, "name": "Local New", "sync_dirty": 1}


def test_remote_sync_category_tombstone_moves_local_channels_to_default(tmp_path: Path) -> None:
    async def _run() -> dict[str, object]:
        db = await open_database(str(tmp_path / "category-tombstone.db"))
        try:
            await init_database(db)
            category = await categories_repo.create_category(db, "Delete Me")
            await db.execute(
                "UPDATE categories SET updated_at = '2026-06-01T00:00:00.000Z' WHERE category_uid = ?",
                (category["category_uid"],),
            )
            await db.commit()
            await channels_repo.add_channel(
                db,
                "UCcatdelete001",
                "Category Delete",
                category_id=int(category["id"]),
            )
            await remote_sync_repo.apply_remote_rows(
                db,
                {
                    "categories": [
                        {
                            "category_uid": category["category_uid"],
                            "name": "Delete Me",
                            "sort_order": 0,
                            "processing_stage": "off",
                            "is_default": 0,
                            "created_at": "2026-06-01T00:00:00.000Z",
                            "updated_at": "2026-06-01T00:00:10.000Z",
                            "deleted_at": "2026-06-01T00:00:10.000Z",
                            "origin_device_id": "remote-device",
                        }
                    ]
                },
            )
            cursor = await db.execute(
                """
                SELECT cat.category_uid, ch.sync_dirty
                FROM channels ch
                JOIN categories cat ON cat.id = ch.category_id
                WHERE ch.channel_id = 'UCcatdelete001'
                """
            )
            row = await cursor.fetchone()
            assert row is not None
            return {
                "category_uid": row["category_uid"],
                "sync_dirty": row["sync_dirty"],
            }
        finally:
            await db.close()

    assert asyncio.run(_run()) == {"category_uid": "default", "sync_dirty": 1}


@pytest.mark.skipif(
    not os.getenv("BRIEFTUBE_TEST_REMOTE_SYNC_DSN"),
    reason="BRIEFTUBE_TEST_REMOTE_SYNC_DSN is not set",
)
def test_remote_sync_real_postgres_schema_smoke() -> None:
    async def _run() -> str:
        gateway = remote_sync_service.RemoteSyncGateway(
            dsn=str(os.environ["BRIEFTUBE_TEST_REMOTE_SYNC_DSN"]),
            connect_timeout_seconds=5,
        )
        await gateway.ensure_schema()
        return "ok"

    assert asyncio.run(_run()) == "ok"


def _sync_config() -> AppConfig:
    return AppConfig(
        remote_sync_enabled=True,
        remote_sync_dsn="postgresql://in-memory.test/db",
        remote_sync_batch_size=100,
        remote_sync_tombstone_retention_days=30,
    )


def _remote_key(table: str, row: Mapping[str, Any]) -> str:
    if table == "categories":
        return str(row.get("category_uid") or "")
    if table == "channels":
        return str(row.get("channel_id") or "")
    return str(row.get("video_id") or "")


def _is_newer(candidate: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
    candidate_updated = str(candidate.get("updated_at") or "")
    existing_updated = str(existing.get("updated_at") or "")
    if candidate_updated != existing_updated:
        return candidate_updated > existing_updated
    candidate_deleted = bool(candidate.get("deleted_at"))
    existing_deleted = bool(existing.get("deleted_at"))
    if candidate_deleted != existing_deleted:
        return candidate_deleted
    return str(candidate.get("origin_device_id") or "") > str(
        existing.get("origin_device_id") or ""
    )


def _parse_dt(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


async def _category_id_by_uid(db, category_uid: str) -> int:
    cursor = await db.execute(
        "SELECT id FROM categories WHERE category_uid = ?",
        (category_uid,),
    )
    row = await cursor.fetchone()
    assert row is not None
    return int(row["id"])


async def _category_name_by_uid(db, category_uid: str) -> str:
    cursor = await db.execute(
        "SELECT name FROM categories WHERE category_uid = ?",
        (category_uid,),
    )
    row = await cursor.fetchone()
    assert row is not None
    return str(row["name"])


async def _read_projection(db, channel_id: str, video_id: str) -> dict[str, object]:
    cursor = await db.execute(
        """
        SELECT
            cat.name AS category_name,
            ch.channel_name,
            v.title AS video_title,
            t.raw_text AS transcript_text,
            a.title AS article_title
        FROM channels ch
        JOIN categories cat ON cat.id = ch.category_id
        JOIN videos v ON v.channel_id = ch.channel_id
        LEFT JOIN transcripts t ON t.video_id = v.video_id
        LEFT JOIN articles a ON a.video_id = v.video_id
        WHERE ch.channel_id = ?
          AND v.video_id = ?
        """,
        (channel_id, video_id),
    )
    row = await cursor.fetchone()
    assert row is not None
    return {key: row[key] for key in row.keys()}


async def _read_tombstones(db, channel_id: str, video_id: str) -> dict[str, object]:
    channel = await _single_value(
        db,
        "SELECT deleted_at FROM channels WHERE channel_id = ?",
        (channel_id,),
    )
    video = await _single_value(
        db,
        "SELECT deleted_at FROM videos WHERE video_id = ?",
        (video_id,),
    )
    transcript = await _single_value(
        db,
        "SELECT deleted_at FROM transcripts WHERE video_id = ?",
        (video_id,),
    )
    article = await _single_value(
        db,
        "SELECT deleted_at FROM articles WHERE video_id = ?",
        (video_id,),
    )
    return {
        "channel_deleted_at": channel,
        "video_deleted_at": video,
        "transcript_deleted_at": transcript,
        "article_deleted_at": article,
    }


async def _single_value(db, sql: str, params: Sequence[object]) -> object:
    cursor = await db.execute(sql, tuple(params))
    row = await cursor.fetchone()
    assert row is not None
    return row[0]
