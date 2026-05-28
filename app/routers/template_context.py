from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import Request

from app.i18n import DEFAULT_LANGUAGE, get_languages, get_texts, normalize_language
from app.repositories import alerts_retention as alerts_repo
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.services.channel_handle import format_channel_handle_display
from app.services.llm import LLM_CODEX_MODEL_OPTIONS
from app.services.llm_capabilities import LlmCapabilityProbe
from app.services.llm_runtime import (
    resolve_llm_runtime_status,
    runtime_reason_text,
    runtime_reason_text_key,
)
from app.state import ALERT_GROUPS_UI_CACHE_KEY, RETENTION_NOTICE_UI_CACHE_KEY_PREFIX
from app.time_utils import format_upload_time
from app.timezone_policy import DEFAULT_TIMEZONE, get_timezone_options, normalize_timezone

ALERT_GROUPS_CACHE_TTL_SECONDS = 5.0
RETENTION_NOTICE_CACHE_TTL_SECONDS = 15.0
CachedValue = TypeVar("CachedValue")


async def _get_cached_ui_value(
    request: Request,
    *,
    key: str,
    ttl_seconds: float,
    loader: Callable[[], Awaitable[CachedValue]],
) -> CachedValue:
    cache = getattr(request.app.state.runtime, "ui_cache", None)
    if not isinstance(cache, dict):
        return await loader()

    now = time.monotonic()
    cached = cache.get(key)
    if (
        isinstance(cached, tuple)
        and len(cached) == 2
        and isinstance(cached[0], (int, float))
        and float(cached[0]) > now
    ):
        return cached[1]

    value = await loader()
    cache[key] = (now + max(0.0, float(ttl_seconds)), value)
    return value


async def _build_llm_runtime_context(
    request: Request,
    txt: dict[str, str],
) -> dict[str, object]:
    llm_settings = await settings_repo.get_llm_settings(request.app.state.runtime.db)
    runtime_issue = await llm_repo.get_llm_runtime_issue(request.app.state.runtime.db)
    pending_count = await llm_repo.count_llm_pending_videos(request.app.state.runtime.db)
    status = resolve_llm_runtime_status(
        llm_client=request.app.state.runtime.llm_client,
        llm_settings=llm_settings,
        runtime_issue=runtime_issue,
        pending_count=pending_count,
    )
    reason_key = runtime_reason_text_key(status.code)
    warning_texts = [runtime_reason_text(code, txt) for code in status.warnings]
    return {
        "ready": status.ready,
        "code": status.code,
        "reason": status.reason,
        "reason_text_key": reason_key,
        "reason_text": runtime_reason_text(status.code, txt),
        "providers_to_try": status.providers_to_try,
        "warnings": status.warnings,
        "warning_texts": warning_texts,
        "pending_count": status.pending_count,
    }


async def _build_llm_capability_context(
    request: Request,
    *,
    current_model: str,
    current_reasoning_effort: str,
    refresh: bool = False,
) -> dict[str, object]:
    runtime = request.app.state.runtime
    probe = getattr(runtime, "llm_capability_probe", None)
    if not isinstance(probe, LlmCapabilityProbe):
        probe = LlmCapabilityProbe(command_exists=lambda _name: False)

    codex = await probe.get_codex_capabilities(refresh=refresh)
    model_options = [(model.value, model.label) for model in codex.models]
    if current_model and current_model not in {value for value, _label in model_options}:
        model_options.append((current_model, current_model))

    effort_options = list(codex.reasoning_efforts)
    if current_reasoning_effort and current_reasoning_effort not in effort_options:
        effort_options.append(current_reasoning_effort)

    return {
        "codex": codex.as_payload(),
        "codex_model_options": tuple(model_options) or LLM_CODEX_MODEL_OPTIONS,
        "codex_reasoning_effort_options": tuple(effort_options),
    }


async def build_template_context(
    request: Request,
    **extra: object,
) -> dict[str, object]:
    include_llm_runtime_status = bool(extra.pop("include_llm_runtime_status", False))
    language_raw = await settings_repo.get_setting(
        request.app.state.runtime.db,
        key="language",
        default=DEFAULT_LANGUAGE,
    )
    language = normalize_language(language_raw)
    timezone_raw = await settings_repo.get_setting(
        request.app.state.runtime.db,
        key="timezone",
        default=DEFAULT_TIMEZONE,
    )
    timezone_name = normalize_timezone(timezone_raw)
    policy_settings = await settings_repo.get_policy_settings(request.app.state.runtime.db)

    alert_groups = await _get_cached_ui_value(
        request,
        key=ALERT_GROUPS_UI_CACHE_KEY,
        ttl_seconds=ALERT_GROUPS_CACHE_TTL_SECONDS,
        loader=lambda: alerts_repo.list_unacknowledged_alert_groups(
            request.app.state.runtime.db,
            limit=5,
        ),
    )

    retention_days = int(policy_settings["retention_days"])
    retention_expired_count = await _get_cached_ui_value(
        request,
        key=f"{RETENTION_NOTICE_UI_CACHE_KEY_PREFIX}{retention_days}",
        ttl_seconds=RETENTION_NOTICE_CACHE_TTL_SECONDS,
        loader=lambda: alerts_repo.count_retention_expired_videos(
            request.app.state.runtime.db,
            retention_days,
        ),
    )
    retention_notice = None
    if retention_expired_count > 0:
        retention_notice = {
            "count": retention_expired_count,
            "retention_days": retention_days,
        }

    txt = get_texts(language)
    llm_settings = extra.get("llm_settings")
    if isinstance(llm_settings, dict):
        llm_model = llm_settings.get("llm_model", {})
        llm_reasoning_effort = llm_settings.get("llm_reasoning_effort", {})
        llm_capabilities = await _build_llm_capability_context(
            request,
            current_model=str(llm_model.get("codex", "")) if isinstance(llm_model, dict) else "",
            current_reasoning_effort=str(llm_reasoning_effort.get("codex", ""))
            if isinstance(llm_reasoning_effort, dict)
            else "",
            refresh=request.query_params.get("llm_capabilities_refresh") == "1",
        )
    else:
        llm_capabilities = {
            "codex": {},
            "codex_model_options": LLM_CODEX_MODEL_OPTIONS,
            "codex_reasoning_effort_options": (),
        }
    context: dict[str, object] = {
        "language": language,
        "timezone": timezone_name,
        "txt": txt,
        "languages": get_languages(),
        "timezones": get_timezone_options(language),
        "alert_groups": alert_groups,
        "policy_settings": policy_settings,
        "retention_notice": retention_notice,
        "llm_capabilities": llm_capabilities,
        "codex_model_options": llm_capabilities["codex_model_options"],
        "codex_reasoning_effort_options": llm_capabilities["codex_reasoning_effort_options"],
        "format_upload_time": format_upload_time,
        "format_channel_handle_display": format_channel_handle_display,
    }
    if include_llm_runtime_status:
        context["llm_runtime_status"] = await _build_llm_runtime_context(
            request,
            txt=txt,
        )
    context.update(extra)
    return context
