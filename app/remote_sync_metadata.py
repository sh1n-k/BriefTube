from __future__ import annotations

import aiosqlite

DEFAULT_CATEGORY_UID = "default"
REMOTE_SYNC_SCHEMA_VERSION = "2"
REMOTE_SYNC_TABLES: tuple[str, ...] = (
    "categories",
    "channels",
    "videos",
    "transcripts",
    "articles",
)
REMOTE_SYNC_KEY_COLUMNS: dict[str, str] = {
    "categories": "category_uid",
    "channels": "channel_id",
    "videos": "video_id",
    "transcripts": "video_id",
    "articles": "video_id",
}
REMOTE_SYNC_PRUNE_ORDER: tuple[str, ...] = tuple(reversed(REMOTE_SYNC_TABLES))
REMOTE_SYNC_DEVICE_ID_KEY = "remote_sync_device_id"
REMOTE_SYNC_RUNTIME_ENABLED_KEY = "remote_sync_runtime_enabled"
REMOTE_SYNC_STATUS_LAST_SUCCESS_AT_KEY = "remote_sync_last_success_at"
REMOTE_SYNC_STATUS_LAST_FAILURE_CODE_KEY = "remote_sync_last_failure_code"
REMOTE_SYNC_STATUS_SCHEMA_VERSION_KEY = "remote_sync_schema_version_status"

SYNC_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
DEVICE_ID_SQL = "COALESCE((SELECT value FROM app_settings WHERE key = 'remote_sync_device_id'), '')"


def sync_dirty_set_clause(*, table_alias: str | None = None) -> str:
    prefix = f"{table_alias}." if table_alias else ""
    origin_expr = f"COALESCE({DEVICE_ID_SQL}, {prefix}origin_device_id, '')"
    return f"updated_at = {SYNC_NOW_SQL}, sync_dirty = 1, origin_device_id = {origin_expr}"


async def is_remote_sync_runtime_enabled(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (REMOTE_SYNC_RUNTIME_ENABLED_KEY,),
    )
    row = await cursor.fetchone()
    return str(row["value"] if row is not None else "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
