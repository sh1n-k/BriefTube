from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import AppConfig
from app.database import init_database, open_database
from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.repositories import remote_sync as remote_sync_repo
from app.services import remote_sync as remote_sync_service


def test_remote_sync_metadata_and_dirty_rows(tmp_path: Path) -> None:
    async def _run() -> dict[str, object]:
        db = await open_database(str(tmp_path / "sync.db"))
        try:
            await init_database(db)
            category = await categories_repo.create_category(db, "Sync Category")
            await channels_repo.add_channel(
                db,
                "UCsync001",
                "Sync Channel",
                category_id=int(category["id"]),
            )
            rows = await remote_sync_repo.list_dirty_rows(db, batch_size=100)
            return {
                "category_uids": [row["category_uid"] for row in rows["categories"]],
                "channel_category_uid": rows["channels"][0]["category_uid"],
                "has_device_id": all(
                    str(row["origin_device_id"] or "")
                    for table_rows in rows.values()
                    for row in table_rows
                ),
            }
        finally:
            await db.close()

    result = asyncio.run(_run())

    assert "default" in result["category_uids"]
    assert result["channel_category_uid"] != ""
    assert result["has_device_id"] is True


def test_remote_sync_applies_remote_rows_with_category_mapping(tmp_path: Path) -> None:
    async def _run() -> dict[str, str | None]:
        db = await open_database(str(tmp_path / "pull.db"))
        try:
            await init_database(db)
            await remote_sync_repo.apply_remote_rows(
                db,
                {
                    "categories": [
                        {
                            "category_uid": "category-remote",
                            "name": "Remote",
                            "sort_order": 3,
                            "processing_stage": "full",
                            "is_default": 0,
                            "created_at": "2026-06-01T00:00:00.000Z",
                            "updated_at": "2026-06-01T00:00:01.000Z",
                            "deleted_at": None,
                            "origin_device_id": "remote-a",
                        }
                    ],
                    "channels": [
                        {
                            "channel_id": "UCremote001",
                            "channel_name": "Remote Channel",
                            "rss_url": "https://example.test/rss",
                            "is_active": 1,
                            "category_uid": "category-remote",
                            "last_seen_published_at": None,
                            "channel_handle": None,
                            "channel_url_canonical": None,
                            "channel_thumbnail_url": None,
                            "channel_description": None,
                            "channel_language_hint": None,
                            "metadata_fetched_at": None,
                            "metadata_fetch_status": "never",
                            "created_at": "2026-06-01T00:00:00.000Z",
                            "updated_at": "2026-06-01T00:00:02.000Z",
                            "deleted_at": None,
                            "origin_device_id": "remote-a",
                        }
                    ],
                },
            )
            cursor = await db.execute(
                """
                SELECT cat.category_uid
                FROM channels ch
                JOIN categories cat ON cat.id = ch.category_id
                WHERE ch.channel_id = 'UCremote001'
                """
            )
            row = await cursor.fetchone()
            return {"category_uid": None if row is None else str(row["category_uid"])}
        finally:
            await db.close()

    result = asyncio.run(_run())

    assert result["category_uid"] == "category-remote"


def test_remote_sync_runtime_enabled_uses_tombstones(tmp_path: Path) -> None:
    async def _run() -> dict[str, object]:
        db = await open_database(str(tmp_path / "tombstone.db"))
        try:
            await init_database(db)
            await remote_sync_repo.set_runtime_enabled(db, True)
            await channels_repo.add_channel(db, "UCtomb001", "Tombstone")
            result = await channels_repo.delete_channels_with_related_data(db, ["UCtomb001"])
            cursor = await db.execute(
                "SELECT deleted_at, sync_dirty FROM channels WHERE channel_id = 'UCtomb001'"
            )
            row = await cursor.fetchone()
            return {
                "deleted_channels": result["deleted_channels"],
                "deleted_at": None if row is None else row["deleted_at"],
                "sync_dirty": None if row is None else row["sync_dirty"],
            }
        finally:
            await db.close()

    result = asyncio.run(_run())

    assert result["deleted_channels"] == 1
    assert result["deleted_at"]
    assert result["sync_dirty"] == 1


def test_remote_sync_push_once_prunes_even_without_dirty_rows(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeGateway:
        def __init__(self, *, dsn: str, connect_timeout_seconds: int) -> None:
            assert dsn == "postgresql://example.test/db"
            assert connect_timeout_seconds == 5

        async def ensure_schema(self) -> None:
            calls.append("ensure_schema")

        async def push(self, rows_by_table) -> None:
            calls.append("push")

        async def prune(self, *, retention_days: int, batch_size: int) -> int:
            calls.append(f"prune:{retention_days}:{batch_size}")
            return 0

    async def _run() -> list[str]:
        db = await open_database(str(tmp_path / "push-once.db"))
        try:
            await init_database(db)
            await remote_sync_repo.mark_rows_pushed(
                db,
                await remote_sync_repo.list_dirty_rows(db, batch_size=100),
            )
            monkeypatch.setattr(remote_sync_service, "RemoteSyncGateway", FakeGateway)
            config = AppConfig(
                remote_sync_enabled=True,
                remote_sync_dsn="postgresql://example.test/db",
                remote_sync_batch_size=25,
                remote_sync_tombstone_retention_days=7,
            )
            await remote_sync_service.run_push_once(config, db)
            return calls
        finally:
            await db.close()

    assert asyncio.run(_run()) == ["ensure_schema", "prune:7:25"]
