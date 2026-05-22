from __future__ import annotations

from typing import Any

TRANSCRIPT_REQUEST_HEADER_PROFILE = "firefox_ko_windows"
TRANSCRIPT_REQUEST_HEADER_KEYS = (
    "User-Agent",
    "Accept",
    "Accept-Language",
    "DNT",
    "Upgrade-Insecure-Requests",
)
TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS: dict[str, str] = {
    "User-Agent": "transcript_header_user_agent",
    "Accept": "transcript_header_accept",
    "Accept-Language": "transcript_header_accept_language",
    "DNT": "transcript_header_dnt",
    "Upgrade-Insecure-Requests": "transcript_header_upgrade_insecure_requests",
}

_DEFAULT_TRANSCRIPT_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.3",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

_MAX_HEADER_LINES = 20
_MAX_HEADER_VALUE_LENGTH = 1024


def default_transcript_request_headers() -> dict[str, str]:
    return dict(_DEFAULT_TRANSCRIPT_REQUEST_HEADERS)


def normalize_header_overrides(raw: dict[str, Any], *, strict: bool) -> dict[str, str]:
    normalized: dict[str, str] = {}
    allowed = set(TRANSCRIPT_REQUEST_HEADER_KEYS)
    for key, value in raw.items():
        header_key = str(key).strip()
        if header_key not in allowed:
            if strict:
                raise ValueError(f"unsupported header key: {header_key}")
            continue
        header_value = str(value).strip()
        if len(header_value) > _MAX_HEADER_VALUE_LENGTH:
            raise ValueError(f"header value is too long: {header_key}")
        normalized[header_key] = header_value
    return normalized


def compact_header_overrides(raw: dict[str, Any], *, strict: bool) -> dict[str, str]:
    defaults = default_transcript_request_headers()
    normalized = normalize_header_overrides(raw, strict=strict)
    compact: dict[str, str] = {}
    for key in TRANSCRIPT_REQUEST_HEADER_KEYS:
        if key not in normalized:
            continue
        value = normalized[key]
        if not value or value == defaults[key]:
            continue
        compact[key] = value
    return compact


def merge_with_default_headers(raw: dict[str, Any]) -> dict[str, str]:
    merged = default_transcript_request_headers()
    normalized = normalize_header_overrides(raw, strict=False)
    for key, value in normalized.items():
        if value:
            merged[key] = value
    return merged


def parse_headers_multiline(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    non_empty_lines = 0
    for line_no, raw_line in enumerate(str(text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        non_empty_lines += 1
        if non_empty_lines > _MAX_HEADER_LINES:
            raise ValueError("too many header lines")
        if ":" not in line:
            raise ValueError(f"invalid header format at line {line_no}")
        key, value = line.split(":", 1)
        header_key = key.strip()
        header_value = value.strip()
        if header_key not in TRANSCRIPT_REQUEST_HEADER_KEYS:
            raise ValueError(f"unsupported header key: {header_key}")
        if len(header_value) > _MAX_HEADER_VALUE_LENGTH:
            raise ValueError(f"header value is too long: {header_key}")
        parsed[header_key] = header_value
    return parsed


def parse_headers_from_fields(raw: dict[str, Any]) -> tuple[dict[str, str], bool]:
    parsed: dict[str, str] = {}
    has_any = False
    for key in TRANSCRIPT_REQUEST_HEADER_KEYS:
        field_name = TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS[key]
        if field_name not in raw:
            continue
        has_any = True
        header_value = str(raw.get(field_name) or "").strip()
        if len(header_value) > _MAX_HEADER_VALUE_LENGTH:
            raise ValueError(f"header value is too long: {key}")
        parsed[key] = header_value
    return parsed, has_any


def validate_complete_header_fields(raw: dict[str, Any]) -> None:
    missing = [
        TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS[key]
        for key in TRANSCRIPT_REQUEST_HEADER_KEYS
        if TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS[key] not in raw
    ]
    if missing:
        raise ValueError("header fields input requires all fixed keys")


def format_headers_multiline(raw: dict[str, Any]) -> str:
    merged = merge_with_default_headers(raw)
    return "\n".join(f"{key}: {merged[key]}" for key in TRANSCRIPT_REQUEST_HEADER_KEYS)
