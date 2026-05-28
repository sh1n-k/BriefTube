from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

import aiosqlite

DEFAULT_CATEGORY_NAME = "미분류"
CATEGORY_PROCESSING_STAGE_OFF = "off"
CATEGORY_PROCESSING_STAGE_TRANSCRIPT_ONLY = "transcript_only"
CATEGORY_PROCESSING_STAGE_FULL = "full"
CATEGORY_PROCESSING_STAGE_OPTIONS = {
    CATEGORY_PROCESSING_STAGE_OFF,
    CATEGORY_PROCESSING_STAGE_TRANSCRIPT_ONLY,
    CATEGORY_PROCESSING_STAGE_FULL,
}


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def normalize_category_processing_stage(
    value: str | None, *, default: str = CATEGORY_PROCESSING_STAGE_OFF
) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CATEGORY_PROCESSING_STAGE_OPTIONS:
        return normalized
    if default in CATEGORY_PROCESSING_STAGE_OPTIONS:
        return default
    return CATEGORY_PROCESSING_STAGE_OFF


def parse_category_processing_stage(value: object | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CATEGORY_PROCESSING_STAGE_OPTIONS:
        return normalized
    allowed = ", ".join(sorted(CATEGORY_PROCESSING_STAGE_OPTIONS))
    raise ValueError(f"processing_stage must be one of: {allowed}")


def next_category_processing_stage(current: str | None) -> str:
    normalized = normalize_category_processing_stage(current)
    if normalized == CATEGORY_PROCESSING_STAGE_OFF:
        return CATEGORY_PROCESSING_STAGE_TRANSCRIPT_ONLY
    if normalized == CATEGORY_PROCESSING_STAGE_TRANSCRIPT_ONLY:
        return CATEGORY_PROCESSING_STAGE_FULL
    return CATEGORY_PROCESSING_STAGE_OFF


async def get_default_category_id(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT id FROM categories WHERE is_default = 1 LIMIT 1")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("default category not found")
    return int(row["id"])


async def list_categories(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT c.id, c.name, c.sort_order, c.processing_stage, c.is_default, c.created_at,
               COUNT(ch.channel_id) AS channel_count
        FROM categories c
        LEFT JOIN channels ch ON ch.category_id = c.id
        GROUP BY c.id
        ORDER BY c.sort_order ASC, c.id ASC
        """
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def create_category(db: aiosqlite.Connection, name: str) -> dict[str, Any]:
    name = str(name).strip()
    if not name:
        raise ValueError("category name must not be empty")
    cursor = await db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM categories"
    )
    row = await cursor.fetchone()
    next_order = int(row["next_order"]) if row else 0
    try:
        await db.execute(
            "INSERT INTO categories (name, sort_order, processing_stage) VALUES (?, ?, ?)",
            (name, next_order, CATEGORY_PROCESSING_STAGE_OFF),
        )
    except (aiosqlite.IntegrityError, sqlite3.IntegrityError) as exc:
        raise ValueError(f"category name already exists: {name}") from exc
    await db.commit()
    cat_cursor = await db.execute(
        "SELECT id, name, sort_order, processing_stage, is_default, created_at FROM categories WHERE name = ?",
        (name,),
    )
    cat_row = await cat_cursor.fetchone()
    return _row_to_dict(cat_row) or {}


async def rename_category(db: aiosqlite.Connection, category_id: int, name: str) -> int:
    name = str(name).strip()
    if not name:
        raise ValueError("category name must not be empty")
    try:
        cursor = await db.execute(
            "UPDATE categories SET name = ? WHERE id = ?",
            (name, category_id),
        )
    except (aiosqlite.IntegrityError, sqlite3.IntegrityError) as exc:
        raise ValueError(f"category name already exists: {name}") from exc
    await db.commit()
    return int(cursor.rowcount or 0)


async def delete_category(db: aiosqlite.Connection, category_id: int) -> dict[str, int]:
    cursor = await db.execute(
        "SELECT is_default FROM categories WHERE id = ?",
        (category_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("category not found")
    if int(row["is_default"]):
        raise ValueError("cannot delete default category")
    default_id = await get_default_category_id(db)
    move_cursor = await db.execute(
        "UPDATE channels SET category_id = ? WHERE category_id = ?",
        (default_id, category_id),
    )
    channels_moved = int(move_cursor.rowcount or 0)
    await db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    await db.commit()
    return {"deleted": 1, "channels_moved": channels_moved}


async def update_category_processing_stage(
    db: aiosqlite.Connection, category_id: int, processing_stage: str
) -> int:
    safe_stage = normalize_category_processing_stage(processing_stage)
    cursor = await db.execute(
        "UPDATE categories SET processing_stage = ? WHERE id = ?",
        (safe_stage, category_id),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def cycle_category_processing_stage(db: aiosqlite.Connection, category_id: int) -> str | None:
    cursor = await db.execute(
        "SELECT processing_stage FROM categories WHERE id = ?",
        (category_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    current_stage = normalize_category_processing_stage(str(row["processing_stage"] or ""))
    next_stage = next_category_processing_stage(current_stage)
    await db.execute(
        "UPDATE categories SET processing_stage = ? WHERE id = ?",
        (next_stage, category_id),
    )
    await db.commit()
    return next_stage


async def reorder_categories(db: aiosqlite.Connection, ordered_ids: list[int]) -> int:
    cursor = await db.execute("SELECT id FROM categories")
    rows = await cursor.fetchall()
    existing_ids = {int(row["id"]) for row in rows}
    input_ids = set(ordered_ids)
    if input_ids != existing_ids:
        missing_in_input = existing_ids - input_ids
        for missing_id in missing_in_input:
            ordered_ids.append(missing_id)
    updated = 0
    for idx, category_id in enumerate(ordered_ids):
        cursor = await db.execute(
            "UPDATE categories SET sort_order = ? WHERE id = ?",
            (idx, category_id),
        )
        updated += int(cursor.rowcount or 0)
    await db.commit()
    return updated


async def move_channels_to_category(
    db: aiosqlite.Connection,
    channel_ids: list[str],
    target_category_id: int,
) -> int:
    normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not normalized:
        return 0
    cat_cursor = await db.execute("SELECT id FROM categories WHERE id = ?", (target_category_id,))
    if await cat_cursor.fetchone() is None:
        raise ValueError("target category not found")
    placeholders = ",".join(["?"] * len(normalized))
    cursor = await db.execute(
        f"UPDATE channels SET category_id = ? WHERE channel_id IN ({placeholders})",
        (target_category_id, *normalized),
    )
    await db.commit()
    return int(cursor.rowcount or 0)
