from __future__ import annotations

import json
from typing import Any, cast

import aiosqlite

import app.repositories._settings as settings_repository

TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY = "transcript_request_headers_overrides_json"

get_settings_map = settings_repository.get_settings_map
get_setting = settings_repository.get_setting
set_setting = settings_repository.set_setting


async def get_transcript_request_header_overrides(db: aiosqlite.Connection) -> dict[str, str]:
    raw = await get_setting(
        db,
        key=TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY,
        default="{}",
    )
    text = str(raw or "").strip() or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    parsed_dict = cast(dict[Any, Any], parsed)
    overrides: dict[str, str] = {}
    for key, value in parsed_dict.items():
        overrides[str(key).strip()] = str(value).strip()
    return overrides


async def save_transcript_request_header_overrides(
    db: aiosqlite.Connection,
    overrides: dict[str, str],
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in overrides.items():
        normalized[str(key).strip()] = str(value).strip()
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    await set_setting(
        db,
        key=TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY,
        value=payload,
    )
    return await get_transcript_request_header_overrides(db)
