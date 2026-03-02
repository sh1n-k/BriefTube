from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Awaitable, Callable, Mapping

LLM_PROVIDER_CODEX = "codex"
LLM_PROVIDER_CLAUDE = "claude"
LLM_PROVIDER_NONE = "none"

LLM_PROVIDER_OPTIONS = {LLM_PROVIDER_CODEX, LLM_PROVIDER_CLAUDE}
LLM_PROVIDER_FALLBACK_OPTIONS = {LLM_PROVIDER_NONE, *LLM_PROVIDER_OPTIONS}
LLM_PROMPT_TEMPLATE_MAX_LENGTH = 20_000
LLM_CODEX_MODEL_FIXED = "gpt-5.3-codex"
LLM_REASONING_EFFORT_OPTIONS = {"low", "medium", "high"}

ARTICLE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "lead": {"type": "string"},
        "body": {"type": "string"},
        "fact_box": {"type": "string"},
        "timestamps": {"type": "string"},
    },
    "required": ["title", "lead", "body"],
    "additionalProperties": False,
}
ARTICLE_JSON_SCHEMA_COMPACT = json.dumps(ARTICLE_JSON_SCHEMA, ensure_ascii=True, separators=(",", ":"))

REFUSAL_KEYWORDS = (
    "prompt injection",
    "프롬프트 인젝션",
    "cannot comply",
    "unable to comply",
    "refuse",
    "refusal",
    "거부",
)
AUTH_KEYWORDS = (
    "not logged in",
    "login required",
    "authentication required",
    "permission denied",
    "unauthorized",
    "forbidden",
    "auth failed",
)


@dataclass(slots=True)
class LlmSettings:
    provider_primary: str
    provider_fallback: str
    prompt_template: str
    llm_model: dict[str, str]
    llm_reasoning_effort: dict[str, str]


@dataclass(slots=True)
class LlmRuntimePlan:
    providers_to_try: list[str]
    blocking_reason: str | None
    warnings: list[str]


@dataclass(slots=True)
class CommandExecutionResult:
    exit_code: int
    stdout: str
    stderr: str


class LlmClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str | None = None,
        retryable: bool = True,
    ):
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.retryable = retryable


CommandRunner = Callable[[list[str], int, str | None], Awaitable[CommandExecutionResult]]
CommandExists = Callable[[str], bool]


def normalize_llm_provider(value: str | None, *, allow_none: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    options = LLM_PROVIDER_FALLBACK_OPTIONS if allow_none else LLM_PROVIDER_OPTIONS
    if normalized in options:
        return normalized
    return LLM_PROVIDER_NONE if allow_none else LLM_PROVIDER_CODEX


def normalize_llm_settings(raw: Mapping[str, Any] | None) -> LlmSettings:
    payload = raw or {}
    primary = normalize_llm_provider(str(payload.get("provider_primary") or ""))
    fallback = normalize_llm_provider(str(payload.get("provider_fallback") or ""), allow_none=True)
    if fallback == primary:
        fallback = LLM_PROVIDER_NONE
    prompt_template = str(payload.get("prompt_template") or "")
    model_payload = payload.get("llm_model")
    if isinstance(model_payload, Mapping):
        claude_model_raw = model_payload.get("claude", "")
    else:
        claude_model_raw = payload.get("llm_model_claude", "")
    claude_model = str(claude_model_raw or "").strip()

    effort_payload = payload.get("llm_reasoning_effort")
    if isinstance(effort_payload, Mapping):
        codex_effort_raw = effort_payload.get("codex", "")
        claude_effort_raw = effort_payload.get("claude", "")
    else:
        codex_effort_raw = payload.get("llm_reasoning_effort_codex", "")
        claude_effort_raw = payload.get("llm_reasoning_effort_claude", "")
    codex_effort = _normalize_reasoning_effort(codex_effort_raw)
    claude_effort = _normalize_reasoning_effort(claude_effort_raw)

    return LlmSettings(
        provider_primary=primary,
        provider_fallback=fallback,
        prompt_template=prompt_template,
        llm_model={
            "codex": LLM_CODEX_MODEL_FIXED,
            "claude": claude_model,
        },
        llm_reasoning_effort={
            "codex": codex_effort,
            "claude": claude_effort,
        },
    )


def _normalize_reasoning_effort(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    if normalized in LLM_REASONING_EFFORT_OPTIONS:
        return normalized
    return ""


async def _default_command_runner(
    args: list[str],
    timeout_seconds: int,
    stdin_text: str | None,
) -> CommandExecutionResult:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        input_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=input_bytes),
            timeout=max(1, int(timeout_seconds)),
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise LlmClientError(
            "llm_timeout",
            f"LLM command timed out after {timeout_seconds}s",
            retryable=True,
        ) from exc

    return CommandExecutionResult(
        exit_code=int(process.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


class UnifiedLlmClient:
    def __init__(
        self,
        timeout_seconds: int,
        runner: CommandRunner | None = None,
        command_exists: CommandExists | None = None,
    ):
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._runner = runner or _default_command_runner
        self._command_exists = command_exists or (lambda name: shutil.which(name) is not None)

    def resolve_runtime_plan(self, settings: Mapping[str, Any] | None) -> LlmRuntimePlan:
        normalized = normalize_llm_settings(settings)
        if not normalized.prompt_template.strip():
            return LlmRuntimePlan(
                providers_to_try=[],
                blocking_reason="llm_prompt_missing",
                warnings=[],
            )

        primary_command = self._provider_command(normalized.provider_primary)
        if not self._command_exists(primary_command):
            return LlmRuntimePlan(
                providers_to_try=[],
                blocking_reason=f"llm_provider_unavailable_{normalized.provider_primary}",
                warnings=[],
            )

        providers_to_try = [normalized.provider_primary]
        warnings: list[str] = []
        fallback = normalized.provider_fallback
        if fallback != LLM_PROVIDER_NONE:
            fallback_command = self._provider_command(fallback)
            if self._command_exists(fallback_command):
                providers_to_try.append(fallback)
            else:
                warnings.append(f"llm_provider_unavailable_{fallback}")

        return LlmRuntimePlan(
            providers_to_try=providers_to_try,
            blocking_reason=None,
            warnings=warnings,
        )

    def runtime_not_ready_reason(self, settings: Mapping[str, Any] | None) -> str | None:
        return self.resolve_runtime_plan(settings).blocking_reason

    def _provider_command(self, provider: str) -> str:
        normalized = normalize_llm_provider(provider, allow_none=True)
        if normalized == LLM_PROVIDER_CODEX:
            return "codex"
        if normalized == LLM_PROVIDER_CLAUDE:
            return "claude"
        raise LlmClientError(
            "llm_provider_invalid",
            f"Unsupported provider: {provider}",
            provider=provider,
            retryable=False,
        )

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

        last_error: LlmClientError | None = None
        for provider in runtime_plan.providers_to_try:
            refused_once = False
            while True:
                try:
                    return await self._invoke_provider(provider, prompt, normalized)
                except LlmClientError as exc:
                    last_error = exc
                    if exc.code == "llm_provider_refused" and not refused_once:
                        refused_once = True
                        continue
                    break

        if last_error is not None:
            raise last_error

        raise LlmClientError("llm_unknown_error", "LLM provider did not return a result")

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

    async def _invoke_provider(self, provider: str, prompt: str, settings: LlmSettings) -> dict[str, str]:
        if provider == LLM_PROVIDER_CODEX:
            return await self._invoke_codex(
                prompt,
                model=settings.llm_model.get("codex", LLM_CODEX_MODEL_FIXED),
                reasoning_effort=settings.llm_reasoning_effort.get("codex", ""),
            )
        if provider == LLM_PROVIDER_CLAUDE:
            return await self._invoke_claude(
                prompt,
                model=settings.llm_model.get("claude", ""),
                reasoning_effort=settings.llm_reasoning_effort.get("claude", ""),
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
        model: str,
        reasoning_effort: str,
    ) -> dict[str, str]:
        if not self._command_exists("codex"):
            raise LlmClientError(
                "llm_provider_unavailable_codex",
                "LLM provider command is not available: codex",
                provider=LLM_PROVIDER_CODEX,
                retryable=False,
            )

        with tempfile.TemporaryDirectory(prefix="brieftube-llm-codex-") as tmpdir:
            schema_file = Path(tmpdir) / "article.schema.json"
            output_file = Path(tmpdir) / "last_message.json"
            schema_file.write_text(ARTICLE_JSON_SCHEMA_COMPACT, encoding="utf-8")

            args = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "-m",
                model or LLM_CODEX_MODEL_FIXED,
                "--output-schema",
                str(schema_file),
                "--output-last-message",
                str(output_file),
                "-",
            ]
            if reasoning_effort:
                args.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
            result = await self._runner(args, self.timeout_seconds, prompt)
            if result.exit_code != 0:
                raise self._classify_command_failure(
                    provider=LLM_PROVIDER_CODEX,
                    stderr=result.stderr,
                    stdout=result.stdout,
                    exit_code=result.exit_code,
                )

            raw_output = ""
            if output_file.exists():
                raw_output = output_file.read_text(encoding="utf-8", errors="replace").strip()
            if not raw_output:
                raw_output = result.stdout

        return self._parse_provider_output(LLM_PROVIDER_CODEX, raw_output)

    async def _invoke_claude(
        self,
        prompt: str,
        *,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, str]:
        if not self._command_exists("claude"):
            raise LlmClientError(
                "llm_provider_unavailable_claude",
                "LLM provider command is not available: claude",
                provider=LLM_PROVIDER_CLAUDE,
                retryable=False,
            )

        args = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            ARTICLE_JSON_SCHEMA_COMPACT,
            "--no-session-persistence",
        ]
        if model:
            args.extend(["--model", model])
        if reasoning_effort:
            args.extend(["--effort", reasoning_effort])
        result = await self._runner(args, self.timeout_seconds, prompt)
        if result.exit_code != 0:
            raise self._classify_command_failure(
                provider=LLM_PROVIDER_CLAUDE,
                stderr=result.stderr,
                stdout=result.stdout,
                exit_code=result.exit_code,
            )

        return self._parse_provider_output(LLM_PROVIDER_CLAUDE, result.stdout)

    def _classify_command_failure(
        self,
        *,
        provider: str,
        stderr: str,
        stdout: str,
        exit_code: int,
    ) -> LlmClientError:
        combined = f"{stderr}\n{stdout}".strip()
        message = self._trim_error_message(combined or f"provider exit code={exit_code}")
        if self._looks_like_auth(message):
            return LlmClientError(
                "llm_provider_auth_required",
                message,
                provider=provider,
                retryable=False,
            )
        if self._looks_like_refusal(message):
            return LlmClientError(
                "llm_provider_refused",
                message,
                provider=provider,
                retryable=False,
            )
        return LlmClientError(
            "llm_provider_command_failed",
            message,
            provider=provider,
            retryable=True,
        )

    def _parse_provider_output(self, provider: str, stdout: str) -> dict[str, str]:
        parsed = self._load_json(stdout, provider)
        payload = self._extract_article_payload(parsed, provider)
        return self._coerce_article(payload, provider=provider)

    def _load_json(self, raw: str, provider: str) -> Any:
        stripped = raw.strip()
        if not stripped:
            raise LlmClientError(
                "llm_schema_invalid",
                "LLM output is empty",
                provider=provider,
                retryable=True,
            )
        if self._looks_like_auth(stripped):
            raise LlmClientError(
                "llm_provider_auth_required",
                self._trim_error_message(stripped),
                provider=provider,
                retryable=False,
            )
        if self._looks_like_refusal(stripped):
            raise LlmClientError(
                "llm_provider_refused",
                self._trim_error_message(stripped),
                provider=provider,
                retryable=False,
            )

        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            for line in reversed(stripped.splitlines()):
                candidate = line.strip()
                if not candidate:
                    continue
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue

        raise LlmClientError(
            "llm_schema_invalid",
            "LLM output is not valid JSON",
            provider=provider,
            retryable=True,
        )

    def _extract_article_payload(self, data: Any, provider: str) -> Mapping[str, Any]:
        if isinstance(data, dict):
            if bool(data.get("is_error")):
                subtype = str(data.get("subtype") or "").strip().lower()
                message = str(data.get("error") or data.get("result") or "provider error").strip()
                message = self._trim_error_message(message)
                if "refus" in subtype or self._looks_like_refusal(message):
                    raise LlmClientError(
                        "llm_provider_refused",
                        message,
                        provider=provider,
                        retryable=False,
                    )
                if self._looks_like_auth(message):
                    raise LlmClientError(
                        "llm_provider_auth_required",
                        message,
                        provider=provider,
                        retryable=False,
                    )
                raise LlmClientError(
                    "llm_provider_command_failed",
                    message,
                    provider=provider,
                    retryable=True,
                )

            if self._is_article_payload(data):
                return data

            structured = data.get("structured_output")
            if isinstance(structured, dict) and self._is_article_payload(structured):
                return structured

            nested_result = data.get("result")
            if isinstance(nested_result, dict) and self._is_article_payload(nested_result):
                return nested_result
            if isinstance(nested_result, str):
                if self._looks_like_auth(nested_result):
                    raise LlmClientError(
                        "llm_provider_auth_required",
                        self._trim_error_message(nested_result),
                        provider=provider,
                        retryable=False,
                    )
                if self._looks_like_refusal(nested_result):
                    raise LlmClientError(
                        "llm_provider_refused",
                        self._trim_error_message(nested_result),
                        provider=provider,
                        retryable=False,
                    )
                nested_result_stripped = nested_result.strip()
                if nested_result_stripped.startswith("{") and nested_result_stripped.endswith("}"):
                    try:
                        nested_json = json.loads(nested_result_stripped)
                    except json.JSONDecodeError:
                        nested_json = None
                    if isinstance(nested_json, dict) and self._is_article_payload(nested_json):
                        return nested_json

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and self._is_article_payload(item):
                    return item

        raise LlmClientError(
            "llm_schema_invalid",
            "LLM response does not match required article schema",
            provider=provider,
            retryable=True,
        )

    def _is_article_payload(self, payload: Mapping[str, Any]) -> bool:
        required = {"title", "lead", "body"}
        return required.issubset(set(payload.keys()))

    def _coerce_article(self, payload: Mapping[str, Any], *, provider: str) -> dict[str, str]:
        title = str(payload.get("title") or "").strip()
        lead = str(payload.get("lead") or "").strip()
        body = str(payload.get("body") or "").strip()
        if not title or not lead or not body:
            raise LlmClientError(
                "llm_schema_invalid",
                "LLM response has empty required fields",
                provider=provider,
                retryable=True,
            )
        return {
            "title": title,
            "lead": lead,
            "body": body,
            "fact_box": str(payload.get("fact_box") or "{}"),
            "timestamps": str(payload.get("timestamps") or "[]"),
        }

    def _looks_like_refusal(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword in lowered for keyword in REFUSAL_KEYWORDS)

    def _looks_like_auth(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword in lowered for keyword in AUTH_KEYWORDS)

    def _trim_error_message(self, text: str, limit: int = 600) -> str:
        trimmed = str(text or "").strip()
        if len(trimmed) <= limit:
            return trimmed
        return trimmed[:limit]
