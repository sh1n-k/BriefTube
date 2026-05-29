from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response

from app.repositories import alerts_retention as alerts_repo
from app.routers.helpers import htmx_trigger_header
from app.routers.template_context import build_template_context
from app.services.llm_runtime import LlmRuntimeStatus, is_runtime_ready_for_resume

router = APIRouter(tags=["views"])


def _llm_runtime_toast_header(message: str, tone: str) -> dict[str, str]:
    return htmx_trigger_header("llm-runtime-toast", {"message": message, "tone": tone})


@router.get("/settings/llm/runtime-status")
async def llm_runtime_status_fragment(request: Request):
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
        headers=_llm_runtime_toast_header(message, tone),
    )


@router.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: int,
    request: Request,
    confirmed: str = Form(default=""),
):
    if confirmed != "on":
        return Response(status_code=400)

    affected = await alerts_repo.acknowledge_alert(
        request.app.state.runtime.db,
        alert_id=alert_id,
    )
    if affected == 0:
        return Response(status_code=404)
    request.app.state.runtime.invalidate_alert_groups_cache()
    return Response(status_code=200)


@router.post("/alerts/ack-group")
async def acknowledge_alert_group(
    request: Request,
    alert_type: str = Form(default=""),
    confirmed: str = Form(default=""),
):
    if confirmed != "on":
        return Response(status_code=400)

    normalized_alert_type = str(alert_type).strip()
    if not normalized_alert_type:
        return Response(status_code=400)

    affected = await alerts_repo.acknowledge_alerts_by_type(
        request.app.state.runtime.db,
        alert_type=normalized_alert_type,
    )
    if affected == 0:
        return Response(status_code=404)
    request.app.state.runtime.invalidate_alert_groups_cache()
    return Response(status_code=200)
