from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import llm_policy as _llm_policy
from app.services.llm_errors import AUTH_KEYWORDS as AUTH_KEYWORDS
from app.services.llm_errors import REFUSAL_KEYWORDS as REFUSAL_KEYWORDS
from app.services.llm_errors import (
    LlmClientError,
    schema_error_code,
)
from app.services.llm_invocation import (
    CommandExecutionResult as CommandExecutionResult,
)
from app.services.llm_invocation import (
    CommandExists,
    CommandRunner,
    default_command_exists,
    default_command_runner,
    provider_command_name,
    run_claude_provider_command,
    run_codex_provider_command,
)
from app.services.llm_payload import (
    coerce_article,
    extract_article_payload,
    is_article_payload,
    load_json,
    parse_provider_output,
)
from app.services.llm_provider_fallback import run_provider_fallback
from app.services.llm_provider_result import (
    parse_and_capture_provider_result,
    raise_for_provider_command_failure,
)
from app.services.llm_runtime import LlmRuntimePlan, resolve_llm_runtime_plan
from app.services.llm_schema import ARTICLE_CORE_KEYS as ARTICLE_CORE_KEYS
from app.services.llm_schema import ARTICLE_FIELD_KEYS as ARTICLE_FIELD_KEYS
from app.services.llm_schema import ARTICLE_JSON_SCHEMA as ARTICLE_JSON_SCHEMA
from app.services.llm_schema import ARTICLE_JSON_SCHEMA_COMPACT as ARTICLE_JSON_SCHEMA_COMPACT
from app.services.llm_schema import (
    build_provider_schema,
)
from app.services.llm_schema import (
    validate_provider_schema as _validate_provider_schema,
)

LLM_CODEX_MODEL_DEFAULT = _llm_policy.LLM_CODEX_MODEL_DEFAULT
LLM_CODEX_MODEL_MAX_LENGTH = _llm_policy.LLM_CODEX_MODEL_MAX_LENGTH
LLM_CODEX_MODEL_OPTIONS = _llm_policy.LLM_CODEX_MODEL_OPTIONS
LLM_CODEX_MODEL_VALUES = _llm_policy.LLM_CODEX_MODEL_VALUES
LLM_CODEX_REASONING_EFFORT_OPTIONS = _llm_policy.LLM_CODEX_REASONING_EFFORT_OPTIONS
LLM_GEMINI_MODEL_DEFAULT = _llm_policy.LLM_GEMINI_MODEL_DEFAULT
LLM_PROMPT_TEMPLATE_MAX_LENGTH = _llm_policy.LLM_PROMPT_TEMPLATE_MAX_LENGTH
LLM_PROVIDER_CLAUDE = _llm_policy.LLM_PROVIDER_CLAUDE
LLM_PROVIDER_CODEX = _llm_policy.LLM_PROVIDER_CODEX
LLM_PROVIDER_FALLBACK_OPTIONS = _llm_policy.LLM_PROVIDER_FALLBACK_OPTIONS
LLM_PROVIDER_GEMINI = _llm_policy.LLM_PROVIDER_GEMINI
LLM_PROVIDER_NONE = _llm_policy.LLM_PROVIDER_NONE
LLM_PROVIDER_OPTIONS = _llm_policy.LLM_PROVIDER_OPTIONS
LLM_REASONING_EFFORT_GEMINI_OPTIONS = _llm_policy.LLM_REASONING_EFFORT_GEMINI_OPTIONS
LLM_REASONING_EFFORT_OPTIONS = _llm_policy.LLM_REASONING_EFFORT_OPTIONS
normalize_codex_model = _llm_policy.normalize_codex_model
normalize_llm_provider = _llm_policy.normalize_llm_provider


@dataclass(slots=True)
class LlmSettings:
    provider_primary: str
    provider_fallback: str
    prompt_template: str
    llm_model: dict[str, str]
    llm_reasoning_effort: dict[str, str]


def normalize_llm_settings(raw: Mapping[str, Any] | None) -> LlmSettings:
    payload = raw or {}
    primary = normalize_llm_provider(str(payload.get("provider_primary") or ""))
    fallback = normalize_llm_provider(str(payload.get("provider_fallback") or ""), allow_none=True)
    if fallback == primary:
        fallback = LLM_PROVIDER_NONE
    prompt_template = str(payload.get("prompt_template") or "")

    model_payload = payload.get("llm_model")
    if isinstance(model_payload, Mapping):
        codex_model_raw = model_payload.get("codex", "")
        claude_model_raw = model_payload.get("claude", "")
        gemini_model_raw = model_payload.get("gemini", "")
    else:
        codex_model_raw = payload.get("llm_model_codex", "")
        claude_model_raw = payload.get("llm_model_claude", "")
        gemini_model_raw = payload.get("llm_model_gemini", "")
    codex_model = normalize_codex_model(codex_model_raw)
    claude_model = str(claude_model_raw or "").strip()
    gemini_model = str(gemini_model_raw or "").strip()
    if not gemini_model:
        gemini_model = LLM_GEMINI_MODEL_DEFAULT

    effort_payload = payload.get("llm_reasoning_effort")
    if isinstance(effort_payload, Mapping):
        codex_effort_raw = effort_payload.get("codex", "")
        claude_effort_raw = effort_payload.get("claude", "")
        gemini_effort_raw = effort_payload.get("gemini", "")
    else:
        codex_effort_raw = payload.get("llm_reasoning_effort_codex", "")
        claude_effort_raw = payload.get("llm_reasoning_effort_claude", "")
        gemini_effort_raw = payload.get("llm_reasoning_effort_gemini", "")
    codex_effort = _normalize_reasoning_effort(codex_effort_raw, provider=LLM_PROVIDER_CODEX)
    claude_effort = _normalize_reasoning_effort(claude_effort_raw, provider=LLM_PROVIDER_CLAUDE)
    gemini_effort = _normalize_reasoning_effort(gemini_effort_raw, provider=LLM_PROVIDER_GEMINI)

    return LlmSettings(
        provider_primary=primary,
        provider_fallback=fallback,
        prompt_template=prompt_template,
        llm_model={
            "codex": codex_model,
            "claude": claude_model,
            "gemini": gemini_model,
        },
        llm_reasoning_effort={
            "codex": codex_effort,
            "claude": claude_effort,
            "gemini": gemini_effort,
        },
    )


def _normalize_reasoning_effort(value: Any, *, provider: str) -> str:
    normalized = str(value or "").strip().lower()
    default = "none" if provider == LLM_PROVIDER_GEMINI else ""
    if not normalized:
        return default
    if provider == LLM_PROVIDER_GEMINI:
        options = LLM_REASONING_EFFORT_GEMINI_OPTIONS
    elif provider == LLM_PROVIDER_CODEX:
        options = LLM_CODEX_REASONING_EFFORT_OPTIONS
    else:
        options = LLM_REASONING_EFFORT_OPTIONS
    if normalized in options:
        return normalized
    return default


class UnifiedLlmClient:
    def __init__(
        self,
        timeout_seconds: int,
        runner: CommandRunner | None = None,
        command_exists: CommandExists | None = None,
        response_capture_dir: str | None = None,
        response_capture_max_chars: int = 200_000,
        capture_full_response_content: bool = False,
    ):
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._runner = runner or default_command_runner
        self._command_exists = command_exists or default_command_exists
        self._response_capture_dir = (
            Path(response_capture_dir).expanduser() if response_capture_dir else None
        )
        self._response_capture_max_chars = max(1_000, int(response_capture_max_chars))
        self._capture_full_response_content = bool(capture_full_response_content)

    def resolve_runtime_plan(self, settings: Mapping[str, Any] | None) -> LlmRuntimePlan:
        normalized = normalize_llm_settings(settings)
        return resolve_llm_runtime_plan(
            settings=normalized,
            command_exists=self._command_exists,
            provider_command=self._provider_command,
            validate_provider_schema=self.validate_provider_schema,
        )

    def runtime_not_ready_reason(self, settings: Mapping[str, Any] | None) -> str | None:
        return self.resolve_runtime_plan(settings).blocking_reason

    def _provider_command(self, provider: str) -> str:
        return provider_command_name(provider)

    async def restructure(
        self,
        source_title: str,
        transcript_text: str,
        settings: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        runtime_plan = self.resolve_runtime_plan(settings)
        if runtime_plan.blocking_reason is not None:
            raise LlmClientError(
                runtime_plan.blocking_reason,
                "LLM runtime is not ready",
                retryable=False,
            )

        normalized = normalize_llm_settings(settings)
        prompt = self._render_prompt(
            normalized.prompt_template,
            source_title=source_title,
            transcript_text=transcript_text,
        )

        async def invoke_provider(provider: str) -> dict[str, str]:
            return await self._invoke_provider(
                provider,
                prompt,
                source_title=source_title,
                settings=normalized,
            )

        result = await run_provider_fallback(
            providers_to_try=runtime_plan.providers_to_try,
            invoke_provider=invoke_provider,
        )
        article = result.article
        provider = result.provider
        article["_llm_provider"] = provider
        article["_llm_model"] = str(normalized.llm_model.get(provider, "") or "")
        article["_llm_reasoning_effort"] = str(
            normalized.llm_reasoning_effort.get(provider, "") or ""
        )
        article["_llm_generated_at"] = datetime.now(UTC).isoformat()
        return article

    def _render_prompt(
        self,
        prompt_template: str,
        *,
        source_title: str,
        transcript_text: str,
    ) -> str:
        rendered = prompt_template.replace("{source_title}", source_title)
        rendered = rendered.replace("{transcript_text}", transcript_text)
        return rendered

    async def _invoke_provider(
        self,
        provider: str,
        prompt: str,
        *,
        source_title: str,
        settings: LlmSettings,
    ) -> dict[str, str]:
        if provider == LLM_PROVIDER_CODEX:
            return await self._invoke_codex(
                prompt,
                source_title=source_title,
                model=settings.llm_model.get("codex", LLM_CODEX_MODEL_DEFAULT),
                reasoning_effort=settings.llm_reasoning_effort.get("codex", ""),
            )
        if provider == LLM_PROVIDER_CLAUDE:
            return await self._invoke_claude(
                prompt,
                source_title=source_title,
                model=settings.llm_model.get("claude", ""),
                reasoning_effort=settings.llm_reasoning_effort.get("claude", ""),
            )
        if provider == LLM_PROVIDER_GEMINI:
            return await self._invoke_gemini(
                prompt,
                source_title=source_title,
                model=settings.llm_model.get("gemini", LLM_GEMINI_MODEL_DEFAULT),
                reasoning_effort=settings.llm_reasoning_effort.get("gemini", "none"),
            )
        raise LlmClientError(
            "llm_provider_invalid",
            f"Unsupported provider: {provider}",
            provider=provider,
            retryable=False,
        )

    async def _invoke_codex(
        self,
        prompt: str,
        *,
        source_title: str,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, str]:
        result = await run_codex_provider_command(
            prompt=prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            schema_json=self._provider_schema_compact(LLM_PROVIDER_CODEX),
            timeout_seconds=self.timeout_seconds,
            runner=self._runner,
            command_exists=self._command_exists,
        )

        raise_for_provider_command_failure(
            provider=LLM_PROVIDER_CODEX,
            source_title=source_title,
            result=result,
            capture_dir=self._response_capture_dir,
            capture_max_chars=self._response_capture_max_chars,
            include_content=self._capture_full_response_content,
        )
        return self._parse_and_capture_provider_output(
            provider=LLM_PROVIDER_CODEX,
            source_title=source_title,
            result=result,
        )

    async def _invoke_claude(
        self,
        prompt: str,
        *,
        source_title: str,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, str]:
        result = await run_claude_provider_command(
            prompt=prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            schema_json=self._provider_schema_compact(LLM_PROVIDER_CLAUDE),
            timeout_seconds=self.timeout_seconds,
            runner=self._runner,
            command_exists=self._command_exists,
        )
        raise_for_provider_command_failure(
            provider=LLM_PROVIDER_CLAUDE,
            source_title=source_title,
            result=result,
            capture_dir=self._response_capture_dir,
            capture_max_chars=self._response_capture_max_chars,
            include_content=self._capture_full_response_content,
        )
        return self._parse_and_capture_provider_output(
            provider=LLM_PROVIDER_CLAUDE,
            source_title=source_title,
            result=result,
        )

    async def _invoke_gemini(
        self,
        prompt: str,
        *,
        source_title: str,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, str]:
        _ = (prompt, source_title, model, reasoning_effort)
        raise LlmClientError(
            schema_error_code(LLM_PROVIDER_GEMINI),
            "Gemini CLI strict output schema enforcement is not available",
            provider=LLM_PROVIDER_GEMINI,
            retryable=False,
        )

    def _parse_and_capture_provider_output(
        self,
        *,
        provider: str,
        source_title: str,
        result,
    ) -> dict[str, str]:
        return parse_and_capture_provider_result(
            provider=provider,
            source_title=source_title,
            result=result,
            capture_dir=self._response_capture_dir,
            capture_max_chars=self._response_capture_max_chars,
            include_content=self._capture_full_response_content,
        )

    def _parse_provider_output(self, provider: str, stdout: str) -> dict[str, str]:
        return parse_provider_output(provider, stdout)

    def _load_json(self, raw: str, provider: str) -> Any:
        return load_json(raw, provider)

    def _extract_article_payload(self, data: Any, provider: str) -> Mapping[str, Any]:
        return extract_article_payload(data, provider)

    def _is_article_payload(self, payload: Mapping[str, Any]) -> bool:
        return is_article_payload(payload)

    def _coerce_article(self, payload: Mapping[str, Any], *, provider: str) -> dict[str, str]:
        return coerce_article(payload, provider=provider)

    def _provider_schema_compact(self, provider: str) -> str:
        schema = self._provider_schema(provider)
        return json.dumps(schema, ensure_ascii=True, separators=(",", ":"))

    def _provider_schema(self, provider: str) -> dict[str, Any]:
        return build_provider_schema(provider)

    def validate_provider_schema(self, provider: str) -> None:
        _validate_provider_schema(provider, schema_builder=self._provider_schema)
