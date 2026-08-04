"""Shared private helpers for repository modules."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

DEFAULT_ERROR_MESSAGE_MAX_LENGTH = 512
DEFAULT_CATEGORY_UID = "default"
UPDATED_AT_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


def row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def normalize_error_message(
    value: str | None,
    *,
    max_length: int = DEFAULT_ERROR_MESSAGE_MAX_LENGTH,
) -> str:
    if not value:
        return ""
    trimmed = str(value).strip()
    if len(trimmed) <= max_length:
        return trimmed
    return trimmed[:max_length]


def thumbnail_url(path: str | None, video_id: str | None = None) -> str | None:
    if not path:
        safe_video_id = str(video_id or "").strip()
        if not safe_video_id:
            return None
        return f"https://i.ytimg.com/vi/{safe_video_id}/hqdefault.jpg"
    filename = Path(path).name
    if not filename:
        safe_video_id = str(video_id or "").strip()
        if not safe_video_id:
            return None
        return f"https://i.ytimg.com/vi/{safe_video_id}/hqdefault.jpg"
    return f"/thumbnails/{filename}"


def with_thumbnail_url(item: dict[str, Any]) -> dict[str, Any]:
    item["thumbnail_url"] = thumbnail_url(
        item.get("thumbnail_path"),
        item.get("video_id"),
    )
    return item
