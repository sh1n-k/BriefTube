"""Settings JSON API endpoints."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.i18n import SUPPORTED_LANGUAGES, get_texts, normalize_language
from app.repositories import downloads as downloads_repo
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.routers.helpers import htmx_trigger_header, parse_bool_input
from app.routers.template_context import build_template_context
from app.services.downloads import is_ffmpeg_available
from app.services.llm_capabilities import LlmCapabilityProbe
from app.services.llm_runtime import (
    LlmRuntimeStatus,
    is_runtime_ready_for_resume,
    resolve_llm_runtime_status,
    runtime_reason_text,
    runtime_reason_text_key,
)
from app.services.telegram import build_telegram_settings_payload, configure_telegram_notifier
from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS,
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    TRANSCRIPT_REQUEST_HEADER_PROFILE,
    compact_header_overrides,
    default_transcript_request_headers,
    format_headers_multiline,
    merge_with_default_headers,
    parse_headers_from_fields,
    parse_headers_multiline,
    validate_complete_header_fields,
)
from app.timezone_policy import SUPPORTED_TIMEZONES, normalize_timezone

logger = logging.getLogger("app.routers.api")

router = APIRouter(tags=["api"])


def _build_transcript_header_payload(overrides: dict[str, str]) -> dict[str, object]:
    compact = compact_header_overrides(overrides, strict=False)
    values = merge_with_default_headers(compact)
    defaults = default_transcript_request_headers()
    return {
        "profile": TRANSCRIPT_REQUEST_HEADER_PROFILE,
        "keys": list(TRANSCRIPT_REQUEST_HEADER_KEYS),
        "field_names": dict(TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS),
        "defaults": defaults,
        "values": values,
        "multiline": format_headers_multiline(values),
    }


def _build_llm_runtime_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("llm-runtime-toast", {"message": message, "tone": tone})


async def _build_telegram_settings_payload_for_request(request: Request) -> dict[str, object]:
    telegram_settings = await settings_repo.get_telegram_settings(request.app.state.runtime.db)
    return build_telegram_settings_payload(
        request.app.state.runtime.config,
        stored_bot_token=telegram_settings["bot_token"],
        stored_chat_id=telegram_settings["chat_id"],
    )


async def _build_telegram_settings_fragment_context(request: Request) -> dict[str, object]:
    telegram_settings = await _build_telegram_settings_payload_for_request(request)
    return await build_template_context(
        request,
        telegram_settings=telegram_settings,
    )


async def _resolve_llm_runtime_status(request: Request) -> dict[str, Any]:
    llm_settings = await settings_repo.get_llm_settings(request.app.state.runtime.db)
    runtime_issue = await llm_repo.get_llm_runtime_issue(request.app.state.runtime.db)
    pending_count = await llm_repo.count_llm_pending_videos(request.app.state.runtime.db)
    status = resolve_llm_runtime_status(
        llm_client=request.app.state.runtime.llm_client,
        llm_settings=llm_settings,
        runtime_issue=runtime_issue,
        pending_count=pending_count,
    )
    return {
        "ready": status.ready,
        "code": status.code,
        "reason": status.reason,
        "reason_text_key": runtime_reason_text_key(status.code),
        "providers_to_try": status.providers_to_try,
        "warnings": status.warnings,
        "pending_count": status.pending_count,
    }


async def _resolve_llm_capabilities(request: Request, *, refresh: bool = False) -> dict[str, Any]:
    probe = getattr(request.app.state.runtime, "llm_capability_probe", None)
    if not isinstance(probe, LlmCapabilityProbe):
        probe = LlmCapabilityProbe(command_exists=lambda _name: False)
    codex = await probe.get_codex_capabilities(refresh=refresh)
    return {
        "codex": codex.as_payload(),
    }


@router.get("/settings")
async def get_settings(request: Request):
    language = await settings_repo.get_setting(
        request.app.state.runtime.db,
        key="language",
        default="ko",
    )
    workers = await settings_repo.get_worker_settings(request.app.state.runtime.db)
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    videos_per_page = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)
    transcript_guard = await transcripts_repo.get_transcript_guard_state(
        request.app.state.runtime.db
    )
    timezone_value = await settings_repo.get_setting(
        request.app.state.runtime.db,
        key="timezone",
        default="Asia/Seoul",
    )
    transcript_request_header_overrides = (
        await transcripts_repo.get_transcript_request_header_overrides(request.app.state.runtime.db)
    )
    transcript_request_headers = _build_transcript_header_payload(
        transcript_request_header_overrides
    )
    download_defaults = await downloads_repo.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    llm_settings = await settings_repo.get_llm_settings(request.app.state.runtime.db)
    llm_runtime_status = await _resolve_llm_runtime_status(request)
    llm_capabilities = await _resolve_llm_capabilities(request)
    telegram_settings = await _build_telegram_settings_payload_for_request(request)
    return {
        "language": normalize_language(language),
        "timezone": normalize_timezone(timezone_value),
        "workers": workers,
        "policy": policy,
        "videos_per_page": videos_per_page,
        "transcript_guard": transcript_guard,
        "transcript_request_headers": transcript_request_headers,
        "download_defaults": download_defaults,
        "llm_settings": llm_settings,
        "llm_runtime_status": llm_runtime_status,
        "llm_capabilities": llm_capabilities,
        "telegram_settings": telegram_settings,
        "ffmpeg_available": is_ffmpeg_available(),
    }


@router.post("/settings/transcript-guard/reset")
async def reset_transcript_guard(request: Request):
    guard = await transcripts_repo.reset_transcript_guard_state(request.app.state.runtime.db)
    return {
        "ok": True,
        "transcript_guard": guard,
    }


@router.put("/settings/transcript-request-headers")
async def set_transcript_request_headers(request: Request):
    content_type = request.headers.get("content-type", "")
    try:
        parsed: dict[str, str] = {}
        has_field_input = False
        raw_text = ""
        if "application/json" in content_type:
            payload = await request.json() or {}
            if isinstance(payload, dict):
                parsed, has_field_input = parse_headers_from_fields(payload)
                raw_text = str(
                    payload.get("headers_text", payload.get("transcript_request_headers", "")) or ""
                )
            if has_field_input and raw_text.strip():
                raise ValueError("mixed header input modes are not allowed")
            if has_field_input:
                validate_complete_header_fields(payload if isinstance(payload, dict) else {})
            elif raw_text.strip():
                parsed = parse_headers_multiline(raw_text)
            else:
                raise ValueError("empty header payload is not allowed")
        else:
            form = await request.form()
            form_payload = {key: form.get(key) for key in form.keys()}
            parsed, has_field_input = parse_headers_from_fields(form_payload)
            raw_text = str(form.get("transcript_request_headers", "") or "")
            if has_field_input and raw_text.strip():
                raise ValueError("mixed header input modes are not allowed")
            if has_field_input:
                validate_complete_header_fields(form_payload)
            elif raw_text.strip():
                parsed = parse_headers_multiline(raw_text)
            else:
                raise ValueError("empty header payload is not allowed")

        overrides = compact_header_overrides(parsed, strict=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved_overrides = await transcripts_repo.save_transcript_request_header_overrides(
        request.app.state.runtime.db,
        overrides,
    )
    applied_values = merge_with_default_headers(saved_overrides)
    request.app.state.runtime.transcript_service.apply_transcript_request_headers(applied_values)
    payload = _build_transcript_header_payload(saved_overrides)
    return {
        "ok": True,
        "transcript_request_headers": payload,
    }


@router.put("/settings/llm")
async def set_llm_settings(request: Request):
    content_type = request.headers.get("content-type", "")
    provider_primary: str | None = None
    provider_fallback: str | None = None
    prompt_template: str | None = None
    llm_model: dict[str, str] | None = None
    llm_reasoning_effort: dict[str, str] | None = None

    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="llm payload must be object")
        if "provider_primary" in payload:
            provider_primary = str(payload.get("provider_primary", "")).strip().lower()
        if "provider_fallback" in payload:
            provider_fallback = str(payload.get("provider_fallback", "")).strip().lower()
        if "prompt_template" in payload:
            prompt_template = str(payload.get("prompt_template", ""))
        if "llm_model" in payload:
            llm_model_payload = payload.get("llm_model")
            if not isinstance(llm_model_payload, dict):
                raise HTTPException(status_code=400, detail="llm_model must be object")
            llm_model = {key: str(value or "") for key, value in llm_model_payload.items()}
        if "llm_reasoning_effort" in payload:
            llm_reasoning_effort_payload = payload.get("llm_reasoning_effort")
            if not isinstance(llm_reasoning_effort_payload, dict):
                raise HTTPException(status_code=400, detail="llm_reasoning_effort must be object")
            llm_reasoning_effort = {
                key: str(value or "") for key, value in llm_reasoning_effort_payload.items()
            }
    else:
        form = await request.form()
        if "llm_provider_primary" in form:
            provider_primary = str(form.get("llm_provider_primary", "")).strip().lower()
        if "llm_provider_fallback" in form:
            provider_fallback = str(form.get("llm_provider_fallback", "")).strip().lower()
        if "llm_prompt_template" in form:
            prompt_template = str(form.get("llm_prompt_template", ""))
        model_keys = ("llm_model_codex", "llm_model_claude", "llm_model_gemini")
        if any(key in form for key in model_keys):
            llm_model = {}
            if "llm_model_codex" in form:
                llm_model["codex"] = str(form.get("llm_model_codex", ""))
            if "llm_model_claude" in form:
                llm_model["claude"] = str(form.get("llm_model_claude", ""))
            if "llm_model_gemini" in form:
                llm_model["gemini"] = str(form.get("llm_model_gemini", ""))
        reasoning_keys = (
            "llm_reasoning_effort_codex",
            "llm_reasoning_effort_claude",
            "llm_reasoning_effort_gemini",
        )
        if any(key in form for key in reasoning_keys):
            llm_reasoning_effort = {}
            if "llm_reasoning_effort_codex" in form:
                llm_reasoning_effort["codex"] = str(form.get("llm_reasoning_effort_codex", ""))
            if "llm_reasoning_effort_claude" in form:
                llm_reasoning_effort["claude"] = str(form.get("llm_reasoning_effort_claude", ""))
            if "llm_reasoning_effort_gemini" in form:
                llm_reasoning_effort["gemini"] = str(form.get("llm_reasoning_effort_gemini", ""))

    if (
        provider_primary is None
        and provider_fallback is None
        and prompt_template is None
        and llm_model is None
        and llm_reasoning_effort is None
    ):
        raise HTTPException(status_code=400, detail="empty llm settings payload")

    try:
        current = await settings_repo.get_llm_settings(request.app.state.runtime.db)
        candidate = await settings_repo.set_llm_settings(
            request.app.state.runtime.db,
            provider_primary=provider_primary,
            provider_fallback=provider_fallback,
            prompt_template=prompt_template,
            llm_model=llm_model,
            llm_reasoning_effort=llm_reasoning_effort,
            persist=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    runtime_plan = request.app.state.runtime.llm_client.resolve_runtime_plan(candidate)
    runtime_reason = str(runtime_plan.blocking_reason or "").strip().lower()
    if runtime_reason.startswith("llm_provider_schema_invalid_"):
        await llm_repo.set_llm_runtime_issue(
            request.app.state.runtime.db,
            code=runtime_reason,
            message="LLM output schema is incompatible",
        )
        alert_created = await llm_repo.ensure_llm_schema_invalid_alert(request.app.state.runtime.db)
        if alert_created:
            request.app.state.runtime.invalidate_alert_groups_cache()
        language = normalize_language(
            await settings_repo.get_setting(
                request.app.state.runtime.db,
                key="language",
                default="ko",
            )
        )
        txt = get_texts(language)
        reason_text = runtime_reason_text(runtime_reason, txt)
        message = txt["settings_llm_runtime_resume_blocked_toast"].format(reason=reason_text)
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "detail": "llm schema preflight failed",
                "code": runtime_reason,
                "llm_settings": current,
            },
            headers=_build_llm_runtime_toast_header(message, "error"),
        )

    saved = await settings_repo.set_llm_settings(
        request.app.state.runtime.db,
        provider_primary=provider_primary,
        provider_fallback=provider_fallback,
        prompt_template=prompt_template,
        llm_model=llm_model,
        llm_reasoning_effort=llm_reasoning_effort,
    )

    runtime_issue = await llm_repo.get_llm_runtime_issue(request.app.state.runtime.db)
    runtime_issue_code = str(runtime_issue.get("code") or "").strip().lower()
    if runtime_issue_code.startswith("llm_provider_schema_invalid_"):
        await llm_repo.clear_llm_runtime_issue(request.app.state.runtime.db)
    await llm_repo.clear_llm_schema_invalid_alert_flag(request.app.state.runtime.db)

    return {
        "ok": True,
        "llm_settings": saved,
    }


@router.put("/settings/telegram")
async def set_telegram_settings(request: Request):
    content_type = request.headers.get("content-type", "")
    bot_token: str | None = None
    chat_id: str | None = None
    clear_bot_token = False
    clear_chat_id = False

    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="telegram payload must be object")
        if "bot_token" in payload:
            bot_token = str(payload.get("bot_token", ""))
        if "chat_id" in payload:
            chat_id = str(payload.get("chat_id", ""))
        if "clear_bot_token" in payload:
            clear_bot_token = parse_bool_input(payload.get("clear_bot_token"), default=False)
        if "clear_chat_id" in payload:
            clear_chat_id = parse_bool_input(payload.get("clear_chat_id"), default=False)
    else:
        form = await request.form()
        if "telegram_bot_token" in form:
            bot_token = str(form.get("telegram_bot_token", ""))
        if "telegram_chat_id" in form:
            chat_id = str(form.get("telegram_chat_id", ""))
        clear_bot_token = parse_bool_input(form.get("telegram_clear_bot_token"), default=False)
        clear_chat_id = parse_bool_input(form.get("telegram_clear_chat_id"), default=False)

    if bot_token is None and chat_id is None and not clear_bot_token and not clear_chat_id:
        raise HTTPException(status_code=400, detail="empty telegram settings payload")

    if not clear_bot_token and bot_token is not None and not str(bot_token).strip():
        bot_token = None
    if not clear_chat_id and chat_id is not None and not str(chat_id).strip():
        chat_id = None

    if bot_token is None and chat_id is None and not clear_bot_token and not clear_chat_id:
        raise HTTPException(status_code=400, detail="empty telegram settings payload")

    try:
        saved = await settings_repo.set_telegram_settings(
            request.app.state.runtime.db,
            bot_token=bot_token,
            chat_id=chat_id,
            clear_bot_token=clear_bot_token,
            clear_chat_id=clear_chat_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    configure_telegram_notifier(
        request.app.state.runtime.telegram_notifier,
        request.app.state.runtime.config,
        stored_bot_token=saved["bot_token"],
        stored_chat_id=saved["chat_id"],
    )
    payload = build_telegram_settings_payload(
        request.app.state.runtime.config,
        stored_bot_token=saved["bot_token"],
        stored_chat_id=saved["chat_id"],
    )
    if request.headers.get("hx-request") == "true":
        context = await _build_telegram_settings_fragment_context(request)
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/telegram_settings_card.html",
            context=context,
        )
    return {
        "ok": True,
        "telegram_settings": payload,
    }


@router.get("/settings/llm/runtime-status")
async def get_llm_runtime_status(request: Request):
    return await _resolve_llm_runtime_status(request)


@router.get("/settings/llm/capabilities")
async def get_llm_capabilities(request: Request, refresh: bool = Query(False)):
    return await _resolve_llm_capabilities(request, refresh=refresh)


@router.post("/settings/llm/resume")
async def resume_llm_runtime(request: Request):
    language = normalize_language(
        await settings_repo.get_setting(
            request.app.state.runtime.db,
            key="language",
            default="ko",
        )
    )
    txt = get_texts(language)
    status_payload = await _resolve_llm_runtime_status(request)
    status = LlmRuntimeStatus(
        ready=bool(status_payload.get("ready")),
        code=str(status_payload.get("code") or ""),
        reason=str(status_payload.get("reason") or ""),
        providers_to_try=list(status_payload.get("providers_to_try") or []),
        warnings=list(status_payload.get("warnings") or []),
        pending_count=int(status_payload.get("pending_count") or 0),
    )
    if not is_runtime_ready_for_resume(status):
        reason_text = runtime_reason_text(str(status_payload.get("code") or ""), txt)
        message = txt["settings_llm_runtime_resume_blocked_toast"].format(reason=reason_text)
        return JSONResponse(
            status_code=409,
            content={"ok": False, "status": status_payload},
            headers=_build_llm_runtime_toast_header(message, "error"),
        )

    pending_count = int(status_payload["pending_count"])
    await llm_repo.clear_llm_runtime_issue(request.app.state.runtime.db)
    if pending_count > 0:
        request.app.state.runtime.llm_wake_event.set()
        message = txt["settings_llm_runtime_resume_requested_toast"].format(count=pending_count)
        tone = "success"
    else:
        message = txt["settings_llm_runtime_resume_no_pending_toast"]
        tone = "info"
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "resumed_count": pending_count,
            "status": status_payload,
        },
        headers=_build_llm_runtime_toast_header(message, tone),
    )


@router.put("/settings/language")
async def set_language(request: Request):
    content_type = request.headers.get("content-type", "")
    value = ""
    if "application/json" in content_type:
        payload = await request.json()
        value = str(payload.get("language", "")).strip().lower()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        value = str((parsed.get("language") or [""])[0]).strip().lower()

    if value not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="language must be one of: ko, en")

    await settings_repo.set_setting(request.app.state.runtime.db, key="language", value=value)
    return {"ok": True, "language": value}


@router.put("/settings/timezone")
async def set_timezone(request: Request):
    content_type = request.headers.get("content-type", "")
    value = ""
    if "application/json" in content_type:
        payload = await request.json()
        value = str(payload.get("timezone", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        value = str((parsed.get("timezone") or [""])[0]).strip()

    if value not in SUPPORTED_TIMEZONES:
        raise HTTPException(status_code=400, detail="unsupported timezone")

    await settings_repo.set_setting(request.app.state.runtime.db, key="timezone", value=value)
    return {"ok": True, "timezone": value}


@router.put("/settings/videos-per-page")
async def set_videos_per_page(request: Request):
    content_type = request.headers.get("content-type", "")
    raw_value = ""
    if "application/json" in content_type:
        payload = await request.json()
        raw_value = str(payload.get("videos_per_page", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        raw_value = str((parsed.get("videos_per_page") or [""])[0]).strip()

    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="videos_per_page must be integer") from exc

    saved = await settings_repo.set_videos_per_page_setting(request.app.state.runtime.db, value)
    return {"ok": True, "videos_per_page": saved}


@router.put("/settings/workers")
async def set_workers(request: Request):
    defaults = settings_repo.WORKER_SETTING_DEFAULTS
    values = await settings_repo.get_worker_settings(request.app.state.runtime.db)
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await request.json()
        workers_payload = payload.get("workers", {})
        for worker in defaults:
            if worker not in workers_payload:
                continue
            values[worker] = parse_bool_input(
                workers_payload.get(worker),
                default=values.get(worker, defaults[worker]),
            )
    else:
        form = await request.form()
        for worker in defaults:
            # HTML checkbox: checked => "on", unchecked => missing.
            values[worker] = parse_bool_input(
                form.get(worker),
                default=False,
            )

    saved = await settings_repo.set_worker_settings(request.app.state.runtime.db, values)
    return {"ok": True, "workers": saved}


@router.put("/settings/policy")
async def set_policy(request: Request):
    content_type = request.headers.get("content-type", "")
    lookback_value: int | None = None
    retention_value: int | None = None
    feed_mode_value: str | None = None

    try:
        if "application/json" in content_type:
            payload = await request.json()
            if "rss_bootstrap_lookback_days" in payload:
                lookback_value = int(payload.get("rss_bootstrap_lookback_days"))
            if "retention_days" in payload:
                retention_value = int(payload.get("retention_days"))
            if "rss_feed_mode" in payload:
                feed_mode_value = str(payload["rss_feed_mode"])
        else:
            form = await request.form()
            lookback_raw = str(form.get("rss_bootstrap_lookback_days", "")).strip()
            retention_raw = str(form.get("retention_days", "")).strip()
            if lookback_raw:
                lookback_value = int(lookback_raw)
            if retention_raw:
                retention_value = int(retention_raw)
            feed_mode_raw = str(form.get("rss_feed_mode", "")).strip()
            if feed_mode_raw:
                feed_mode_value = feed_mode_raw
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="policy values must be integers") from exc

    saved = await settings_repo.set_policy_settings(
        request.app.state.runtime.db,
        rss_bootstrap_lookback_days=lookback_value,
        retention_days=retention_value,
        rss_feed_mode=feed_mode_value,
    )
    if retention_value is not None:
        request.app.state.runtime.invalidate_retention_notice_cache()
    return {"ok": True, "policy": saved}
