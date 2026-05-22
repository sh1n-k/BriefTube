from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.timezone_policy import normalize_timezone


def format_upload_time(value: str | None, timezone_name: str | None = None) -> str:
    if not value:
        return ""

    tz_name = normalize_timezone(timezone_name)

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        localized = parsed.astimezone(ZoneInfo(tz_name))
        return localized.strftime("%Y-%m-%d %H:%M")
    except Exception:
        # Fallback for unexpected formats.
        text = value.replace("T", " ")
        text = text.split("+")[0].split("Z")[0]
        return text[:16] if len(text) >= 16 else text
