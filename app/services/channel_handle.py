from __future__ import annotations

from urllib.parse import unquote


def format_channel_handle_display(raw_handle: str | None) -> str:
    if raw_handle is None:
        return ""
    value = str(raw_handle).strip()
    if not value:
        return ""
    if value.startswith("@"):
        decoded = unquote(value[1:])
        return f"@{decoded}" if decoded else value
    decoded = unquote(value)
    return decoded if decoded else value
