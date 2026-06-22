from __future__ import annotations

import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse

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


def safe_int(value: object | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_optional_int(value: object | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return None


def build_rss_poll_preview(
    *,
    config: object,
    channel_counts: dict[str, int] | None,
) -> dict[str, object]:
    active_count = int((channel_counts or {}).get("active", 0) or 0)
    inactive_count = int((channel_counts or {}).get("inactive", 0) or 0)
    polling_interval_seconds = max(
        60.0,
        float(getattr(config, "polling_interval_minutes", 15)) * 60.0,
    )
    preview: dict[str, object] = {
        "active_count": active_count,
        "inactive_count": inactive_count,
        "polling_interval_minutes": polling_interval_seconds / 60.0,
        "average_request_interval_seconds": None,
        "min_request_interval_seconds": None,
        "max_request_interval_seconds": None,
        "status": "idle",
    }
    if active_count <= 0:
        return preview

    jitter_ratio = 0.3
    average_seconds = polling_interval_seconds / active_count
    preview.update(
        average_request_interval_seconds=average_seconds,
        min_request_interval_seconds=max(0.1, average_seconds * (1.0 - jitter_ratio)),
        max_request_interval_seconds=average_seconds * (1.0 + jitter_ratio),
        status=(
            "danger" if average_seconds < 2.0 else "warning" if average_seconds < 5.0 else "normal"
        ),
    )
    return preview


def htmx_trigger_header(event_name: str, payload: dict[str, object]) -> dict[str, str]:
    return {"HX-Trigger": json.dumps({event_name: payload}, ensure_ascii=True)}


def full_page_redirect_for_non_fragment_request(
    request: Request,
    redirect_url: str,
) -> RedirectResponse | None:
    if (
        request.headers.get("HX-Request", "").strip().lower() == "true"
        or request.headers.get("X-Requested-With", "").strip() == "BriefTubePoll"
    ):
        return None
    return RedirectResponse(redirect_url, status_code=303)


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
