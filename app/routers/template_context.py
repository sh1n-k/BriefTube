from __future__ import annotations

from fastapi import Request

from app import repository
from app.i18n import DEFAULT_LANGUAGE, get_languages, get_texts, normalize_language
from app.time_utils import format_upload_time
from app.timezone_policy import DEFAULT_TIMEZONE, get_timezone_options, normalize_timezone


async def build_template_context(request: Request, **extra: object) -> dict[str, object]:
    language_raw = await repository.get_setting(
        request.app.state.runtime.db,
        key="language",
        default=DEFAULT_LANGUAGE,
    )
    language = normalize_language(language_raw)
    timezone_raw = await repository.get_setting(
        request.app.state.runtime.db,
        key="timezone",
        default=DEFAULT_TIMEZONE,
    )
    timezone_name = normalize_timezone(timezone_raw)
    alert_groups = await repository.list_unacknowledged_alert_groups(request.app.state.runtime.db, limit=5)
    policy_settings = await repository.get_policy_settings(request.app.state.runtime.db)
    retention_expired_count = await repository.count_retention_expired_videos(
        request.app.state.runtime.db,
        policy_settings["retention_days"],
    )
    retention_notice = None
    if retention_expired_count > 0:
        retention_notice = {
            "count": retention_expired_count,
            "retention_days": policy_settings["retention_days"],
        }

    context: dict[str, object] = {
        "language": language,
        "timezone": timezone_name,
        "txt": get_texts(language),
        "languages": get_languages(),
        "timezones": get_timezone_options(language),
        "alert_groups": alert_groups,
        "policy_settings": policy_settings,
        "retention_notice": retention_notice,
        "format_upload_time": format_upload_time,
    }
    context.update(extra)
    return context
