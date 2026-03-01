from __future__ import annotations

from fastapi import Request

from app import repository
from app.i18n import DEFAULT_LANGUAGE, get_languages, get_texts, normalize_language
from app.services.llm_runtime import resolve_llm_runtime_status, runtime_reason_text_key
from app.time_utils import format_upload_time
from app.timezone_policy import DEFAULT_TIMEZONE, get_timezone_options, normalize_timezone


async def _build_llm_runtime_context(
    request: Request,
    txt: dict[str, str],
) -> dict[str, object]:
    llm_settings = await repository.get_llm_settings(request.app.state.runtime.db)
    runtime_issue = await repository.get_llm_runtime_issue(request.app.state.runtime.db)
    pending_count = await repository.count_llm_pending_videos(request.app.state.runtime.db)
    status = resolve_llm_runtime_status(
        llm_client=request.app.state.runtime.llm_client,
        llm_settings=llm_settings,
        runtime_issue=runtime_issue,
        pending_count=pending_count,
    )
    reason_key = runtime_reason_text_key(status.code)
    warning_texts = [
        txt.get(runtime_reason_text_key(code), txt["settings_llm_runtime_reason_generic"])
        for code in status.warnings
    ]
    return {
        "ready": status.ready,
        "code": status.code,
        "reason": status.reason,
        "reason_text_key": reason_key,
        "reason_text": txt.get(reason_key, txt["settings_llm_runtime_reason_generic"]),
        "providers_to_try": status.providers_to_try,
        "warnings": status.warnings,
        "warning_texts": warning_texts,
        "pending_count": status.pending_count,
    }


async def build_template_context(
    request: Request,
    *,
    include_llm_runtime_status: bool = False,
    **extra: object,
) -> dict[str, object]:
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

    txt = get_texts(language)
    context: dict[str, object] = {
        "language": language,
        "timezone": timezone_name,
        "txt": txt,
        "languages": get_languages(),
        "timezones": get_timezone_options(language),
        "alert_groups": alert_groups,
        "policy_settings": policy_settings,
        "retention_notice": retention_notice,
        "format_upload_time": format_upload_time,
    }
    if include_llm_runtime_status:
        context["llm_runtime_status"] = await _build_llm_runtime_context(
            request,
            txt=txt,
        )
    context.update(extra)
    return context
