from __future__ import annotations

DEFAULT_TIMEZONE = "Asia/Seoul"

_COMMON_TIMEZONES = [
    ("Asia/Seoul", "한국/서울 (KST)", "Asia/Seoul (KST)"),
    ("UTC", "UTC", "UTC"),
    ("America/New_York", "미국/뉴욕 (ET)", "America/New_York (ET)"),
    ("America/Los_Angeles", "미국/로스앤젤레스 (PT)", "America/Los_Angeles (PT)"),
    ("Europe/London", "영국/런던", "Europe/London"),
]

SUPPORTED_TIMEZONES = {item[0] for item in _COMMON_TIMEZONES}


def normalize_timezone(value: str | None) -> str:
    if not value:
        return DEFAULT_TIMEZONE
    text = value.strip()
    if text in SUPPORTED_TIMEZONES:
        return text
    return DEFAULT_TIMEZONE


def get_timezone_options(language: str) -> list[dict[str, str]]:
    use_ko = language.strip().lower() == "ko"
    return [
        {
            "value": tz,
            "label": label_ko if use_ko else label_en,
        }
        for tz, label_ko, label_en in _COMMON_TIMEZONES
    ]
