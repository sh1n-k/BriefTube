from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class LlmRuntimeStatus:
    ready: bool
    code: str
    reason: str
    providers_to_try: list[str]
    warnings: list[str]
    pending_count: int


def resolve_llm_runtime_status(
    *,
    llm_client: Any,
    llm_settings: Mapping[str, Any] | None,
    runtime_issue: Mapping[str, Any] | None,
    pending_count: int,
) -> LlmRuntimeStatus:
    plan = llm_client.resolve_runtime_plan(llm_settings)
    providers_to_try = [str(item) for item in plan.providers_to_try]
    warnings = [str(item) for item in plan.warnings]
    safe_pending = max(0, int(pending_count))
    if plan.blocking_reason:
        code = str(plan.blocking_reason)
        return LlmRuntimeStatus(
            ready=False,
            code=code,
            reason=code,
            providers_to_try=providers_to_try,
            warnings=warnings,
            pending_count=safe_pending,
        )

    issue = runtime_issue or {}
    issue_code = str(issue.get("code") or "").strip()
    issue_message = str(issue.get("message") or "").strip()
    if safe_pending > 0 and _is_runtime_blocking_issue(issue_code):
        return LlmRuntimeStatus(
            ready=False,
            code=issue_code,
            reason=issue_message or issue_code,
            providers_to_try=providers_to_try,
            warnings=warnings,
            pending_count=safe_pending,
        )

    return LlmRuntimeStatus(
        ready=True,
        code="",
        reason="",
        providers_to_try=providers_to_try,
        warnings=warnings,
        pending_count=safe_pending,
    )


def is_runtime_ready_for_resume(status: LlmRuntimeStatus) -> bool:
    normalized = str(status.code or "").strip().lower()
    if normalized == "llm_prompt_missing":
        return False
    if normalized.startswith("llm_provider_unavailable_"):
        return False
    return True


def runtime_reason_text_key(code: str) -> str:
    normalized = str(code or "").strip().lower()
    if normalized == "llm_prompt_missing":
        return "settings_llm_runtime_reason_prompt_missing"
    if normalized == "llm_provider_auth_required":
        return "settings_llm_runtime_reason_auth_required"
    if normalized == "llm_provider_unavailable_codex":
        return "settings_llm_runtime_reason_codex_unavailable"
    if normalized == "llm_provider_unavailable_claude":
        return "settings_llm_runtime_reason_claude_unavailable"
    if normalized.startswith("llm_provider_unavailable_"):
        return "settings_llm_runtime_reason_provider_unavailable"
    return "settings_llm_runtime_reason_generic"


def _is_runtime_blocking_issue(code: str) -> bool:
    normalized = str(code or "").strip().lower()
    if not normalized:
        return False
    if normalized == "llm_provider_auth_required":
        return True
    return normalized.startswith("llm_provider_unavailable_")
