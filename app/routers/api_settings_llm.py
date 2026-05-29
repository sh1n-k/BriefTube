from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.i18n import get_texts, normalize_language
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.routers.helpers import htmx_trigger_header
from app.services.llm_capabilities import resolve_codex_capabilities
from app.services.llm_runtime import (
    LlmRuntimeStatus,
    is_runtime_ready_for_resume,
    resolve_llm_runtime_status,
    runtime_reason_text,
    runtime_reason_text_key,
)

router = APIRouter(tags=["api"])


def _llm_runtime_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("llm-runtime-toast", {"message": message, "tone": tone})


async def resolve_llm_runtime_status_payload(request: Request) -> dict[str, Any]:
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


async def resolve_llm_capabilities_payload(
    request: Request, *, refresh: bool = False
) -> dict[str, Any]:
    codex = await resolve_codex_capabilities(request.app.state.runtime, refresh=refresh)
    return {
        "codex": codex.as_payload(),
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
            headers=_llm_runtime_toast_header(message, "error"),
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


@router.get("/settings/llm/runtime-status")
async def get_llm_runtime_status(request: Request):
    return await resolve_llm_runtime_status_payload(request)


@router.get("/settings/llm/capabilities")
async def get_llm_capabilities(request: Request, refresh: bool = Query(False)):
    return await resolve_llm_capabilities_payload(request, refresh=refresh)


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
    status_payload = await resolve_llm_runtime_status_payload(request)
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
            headers=_llm_runtime_toast_header(message, "error"),
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
        headers=_llm_runtime_toast_header(message, tone),
    )
