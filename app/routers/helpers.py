from __future__ import annotations

import json
from pathlib import Path

from fastapi import Request

from app.i18n import DEFAULT_LANGUAGE, get_texts, normalize_language
from app.repositories import settings as settings_repo


def parse_bool_input(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return None


def htmx_trigger_header(event_name: str, payload: dict[str, object]) -> dict[str, str]:
    return {"HX-Trigger": json.dumps({event_name: payload}, ensure_ascii=True)}


async def request_texts(request: Request) -> dict[str, str]:
    language = normalize_language(
        await settings_repo.get_setting(
            request.app.state.runtime.db,
            key="language",
            default=DEFAULT_LANGUAGE,
        )
    )
    return get_texts(language)


def cleanup_thumbnail_files(thumbnail_paths: list[str], thumbnail_dir: str) -> None:
    base_dir = Path(thumbnail_dir).resolve()
    for raw_path in thumbnail_paths:
        filename = Path(raw_path).name
        if not filename:
            continue
        target = (base_dir / filename).resolve()
        if target.parent != base_dir:
            continue
        try:
            if target.exists() and target.is_file():
                target.unlink()
        except OSError:
            continue
