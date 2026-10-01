from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.i18n import get_texts, normalize_language
from app.llm_policy import LLM_PROVIDER_VALUES
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.routers.helpers import llm_runtime_toast_header
from app.services.llm_runtime import runtime_reason_text

router = APIRouter(tags=["api"])
_ALLOWED_MODEL_KEYS = frozenset({"codex", "grok", "cursor"})
# Cursor encodes reasoning effort in the model slug.
_ALLOWED_REASONING_EFFORT_KEYS = frozenset({"codex", "grok"})


def _provider_model_setting(
    payload: dict[str, Any], field: str, *, allowed: frozenset[str]
) -> dict[str, str]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{field} must be object")
    keys = set(value.keys())
    if not keys or not keys.issubset(allowed):
        raise HTTPException(
            status_code=400,
            detail=f"{field} must contain only {', '.join(sorted(allowed))}",
        )
    return {str(key): str(value.get(key) or "") for key in keys}


@router.put("/settings/llm")
async def set_llm_settings(request: Request):
    content_type = request.headers.get("content-type", "")
    provider_primary: str | None = None
    prompt_template: str | None = None
    llm_model: dict[str, str] | None = None
    llm_reasoning_effort: dict[str, str] | None = None
    max_concurrent: str | None = None

    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="llm payload must be object")
        if "provider_primary" in payload:
            provider_primary = str(payload.get("provider_primary", "")).strip().lower()
            if provider_primary not in LLM_PROVIDER_VALUES:
                allowed = ", ".join(sorted(LLM_PROVIDER_VALUES))
                raise HTTPException(
                    status_code=400,
                    detail=f"provider_primary must be one of: {allowed}",
                )
        if (
            "provider_fallback" in payload
            and str(payload.get("provider_fallback", "none")).strip().lower() != "none"
        ):
            raise HTTPException(status_code=400, detail="fallback provider is not supported")
        if "prompt_template" in payload:
            prompt_template = str(payload.get("prompt_template", ""))
        if "max_concurrent" in payload:
            max_concurrent = str(payload.get("max_concurrent", "")).strip()
        if "llm_model" in payload:
            llm_model = _provider_model_setting(payload, "llm_model", allowed=_ALLOWED_MODEL_KEYS)
        if "llm_reasoning_effort" in payload:
            llm_reasoning_effort = _provider_model_setting(
                payload, "llm_reasoning_effort", allowed=_ALLOWED_REASONING_EFFORT_KEYS
            )
    else:
        form = await request.form()
        if "llm_provider_primary" in form:
            provider_primary = str(form.get("llm_provider_primary", "")).strip().lower()
            if provider_primary not in LLM_PROVIDER_VALUES:
                allowed = ", ".join(sorted(LLM_PROVIDER_VALUES))
                raise HTTPException(
                    status_code=400,
                    detail=f"provider_primary must be one of: {allowed}",
                )
        if (
            "llm_provider_fallback" in form
            and str(form.get("llm_provider_fallback", "none")).strip().lower() != "none"
        ):
            raise HTTPException(status_code=400, detail="fallback provider is not supported")
        if "llm_prompt_template" in form:
            prompt_template = str(form.get("llm_prompt_template", ""))
        if "llm_max_concurrent" in form:
            max_concurrent = str(form.get("llm_max_concurrent", "")).strip()
        model_updates: dict[str, str] = {}
        if "llm_model_codex" in form:
            model_updates["codex"] = str(form.get("llm_model_codex", ""))
        if "llm_model_grok" in form:
            model_updates["grok"] = str(form.get("llm_model_grok", ""))
        if "llm_model_cursor" in form:
            model_updates["cursor"] = str(form.get("llm_model_cursor", ""))
        if model_updates:
            llm_model = model_updates
        effort_updates: dict[str, str] = {}
        if "llm_reasoning_effort_codex" in form:
            effort_updates["codex"] = str(form.get("llm_reasoning_effort_codex", ""))
        if "llm_reasoning_effort_grok" in form:
            effort_updates["grok"] = str(form.get("llm_reasoning_effort_grok", ""))
        if effort_updates:
            llm_reasoning_effort = effort_updates

    if (
        provider_primary is None
        and prompt_template is None
        and llm_model is None
        and llm_reasoning_effort is None
        and max_concurrent is None
    ):
        raise HTTPException(status_code=400, detail="empty llm settings payload")

    try:
        current = await settings_repo.get_llm_settings(request.app.state.runtime.db)
        candidate = await settings_repo.set_llm_settings(
            request.app.state.runtime.db,
            provider_primary=provider_primary,
            prompt_template=prompt_template,
            llm_model=llm_model,
            llm_reasoning_effort=llm_reasoning_effort,
            max_concurrent=max_concurrent,
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
            headers=llm_runtime_toast_header(message, "error"),
        )

    saved = await settings_repo.set_llm_settings(
        request.app.state.runtime.db,
        provider_primary=provider_primary,
        prompt_template=prompt_template,
        llm_model=llm_model,
        llm_reasoning_effort=llm_reasoning_effort,
        max_concurrent=max_concurrent,
    )

    runtime_issue = await llm_repo.get_llm_runtime_issue(request.app.state.runtime.db)
    runtime_issue_code = str(runtime_issue.get("code") or "").strip().lower()
    if runtime_issue_code.startswith("llm_provider_schema_invalid_"):
        await llm_repo.clear_llm_runtime_issue(request.app.state.runtime.db)
    await llm_repo.clear_llm_schema_invalid_alert_flag(request.app.state.runtime.db)

    return {"ok": True, "llm_settings": saved}
