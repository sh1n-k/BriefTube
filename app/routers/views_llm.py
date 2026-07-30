from __future__ import annotations

from fastapi import APIRouter, Request

from app.routers.helpers import (
    full_page_redirect_for_non_fragment_request,
    llm_runtime_toast_header,
)
from app.routers.template_context import build_template_context
from app.services.llm_runtime import LlmRuntimeStatus, is_runtime_ready_for_resume

router = APIRouter(tags=["views"])


@router.get("/settings/llm/runtime-status")
async def llm_runtime_status_fragment(request: Request):
    redirect = full_page_redirect_for_non_fragment_request(request, "/settings")
    if redirect is not None:
        return redirect

    context = await build_template_context(
        request,
        include_llm_runtime_status=True,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/llm_runtime_status.html",
        context=context,
    )


@router.post("/settings/llm/resume")
async def resume_llm_runtime(request: Request):
    context = await build_template_context(
        request,
        include_llm_runtime_status=True,
    )
    txt = context["txt"]
    llm_runtime_status = context["llm_runtime_status"]
    if isinstance(txt, dict) and isinstance(llm_runtime_status, dict):
        status = LlmRuntimeStatus(
            ready=bool(llm_runtime_status.get("ready")),
            code=str(llm_runtime_status.get("code") or ""),
            reason=str(llm_runtime_status.get("reason") or ""),
            providers_to_try=list(llm_runtime_status.get("providers_to_try") or []),
            warnings=list(llm_runtime_status.get("warnings") or []),
            pending_count=int(llm_runtime_status.get("pending_count") or 0),
        )
        if is_runtime_ready_for_resume(status):
            pending_count = int(llm_runtime_status.get("pending_count") or 0)
            if pending_count > 0:
                request.app.state.runtime.llm_wake_event.set()
                message = txt["settings_llm_runtime_resume_requested_toast"].format(
                    count=pending_count
                )
                tone = "success"
            else:
                message = txt["settings_llm_runtime_resume_no_pending_toast"]
                tone = "info"
        else:
            reason = str(
                llm_runtime_status.get("reason_text") or txt["settings_llm_runtime_reason_generic"]
            )
            message = txt["settings_llm_runtime_resume_blocked_toast"].format(reason=reason)
            tone = "error"
    else:
        message = "LLM runtime status is unavailable"
        tone = "error"

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/llm_runtime_status.html",
        context=context,
        headers=llm_runtime_toast_header(message, tone),
    )
