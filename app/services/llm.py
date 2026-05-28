from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import llm_policy as _llm_policy

LLM_CODEX_MODEL_DEFAULT = _llm_policy.LLM_CODEX_MODEL_DEFAULT
LLM_CODEX_MODEL_OPTIONS = _llm_policy.LLM_CODEX_MODEL_OPTIONS
LLM_CODEX_MODEL_VALUES = _llm_policy.LLM_CODEX_MODEL_VALUES
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

ARTICLE_FIELD_KEYS: tuple[str, ...] = ("title", "lead", "body", "fact_box", "timestamps")
ARTICLE_CORE_KEYS: tuple[str, ...] = ("title", "lead", "body")

ARTICLE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in ARTICLE_FIELD_KEYS},
    "required": list(ARTICLE_FIELD_KEYS),
    "additionalProperties": False,
}
ARTICLE_JSON_SCHEMA_COMPACT = json.dumps(
    ARTICLE_JSON_SCHEMA, ensure_ascii=True, separators=(",", ":")
)

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
    else:
        options = LLM_REASONING_EFFORT_OPTIONS
    if normalized in options:
        return normalized
    return default


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
    except TimeoutError as exc:
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
        response_capture_dir: str | None = None,
        response_capture_max_chars: int = 200_000,
        capture_full_response_content: bool = False,
    ):
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._runner = runner or _default_command_runner
        self._command_exists = command_exists or (lambda name: shutil.which(name) is not None)
        self._response_capture_dir = (
            Path(response_capture_dir).expanduser() if response_capture_dir else None
        )
        self._response_capture_max_chars = max(1_000, int(response_capture_max_chars))
        self._capture_full_response_content = bool(capture_full_response_content)

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
        try:
            self.validate_provider_schema(normalized.provider_primary)
        except LlmClientError as exc:
            return LlmRuntimePlan(
                providers_to_try=[],
                blocking_reason=str(exc.code),
                warnings=[],
            )

        providers_to_try = [normalized.provider_primary]
        warnings: list[str] = []
        fallback = normalized.provider_fallback
        if fallback != LLM_PROVIDER_NONE:
            fallback_command = self._provider_command(fallback)
            if self._command_exists(fallback_command):
                try:
                    self.validate_provider_schema(fallback)
                    providers_to_try.append(fallback)
                except LlmClientError as exc:
                    warnings.append(str(exc.code))
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
        if normalized == LLM_PROVIDER_GEMINI:
            return "gemini"
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
                    article = await self._invoke_provider(
                        provider,
                        prompt,
                        source_title=source_title,
                        settings=normalized,
                    )
                    article["_llm_provider"] = provider
                    article["_llm_model"] = str(normalized.llm_model.get(provider, "") or "")
                    article["_llm_reasoning_effort"] = str(
                        normalized.llm_reasoning_effort.get(provider, "") or ""
                    )
                    article["_llm_generated_at"] = datetime.now(UTC).isoformat()
                    return article
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
            schema_file.write_text(
                self._provider_schema_compact(LLM_PROVIDER_CODEX), encoding="utf-8"
            )

            args = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "-m",
                normalize_codex_model(model),
                "--output-schema",
                str(schema_file),
                "--output-last-message",
                str(output_file),
                "-",
            ]
            if reasoning_effort:
                args.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])

            result = await self._runner(args, self.timeout_seconds, prompt)
            raw_output = ""
            if output_file.exists():
                raw_output = output_file.read_text(encoding="utf-8", errors="replace").strip()
            if not raw_output:
                raw_output = result.stdout

        if result.exit_code != 0:
            classified = self._classify_command_failure(
                provider=LLM_PROVIDER_CODEX,
                stderr=result.stderr,
                stdout=result.stdout,
                exit_code=result.exit_code,
            )
            self._capture_provider_response(
                provider=LLM_PROVIDER_CODEX,
                source_title=source_title,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                raw_output=raw_output,
                parse_error_code=classified.code,
                parse_error_message=str(classified),
            )
            raise classified

        return self._parse_and_capture_provider_output(
            provider=LLM_PROVIDER_CODEX,
            source_title=source_title,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            raw_output=raw_output,
        )

    async def _invoke_claude(
        self,
        prompt: str,
        *,
        source_title: str,
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
            self._provider_schema_compact(LLM_PROVIDER_CLAUDE),
            "--no-session-persistence",
        ]
        if model:
            args.extend(["--model", model])
        if reasoning_effort:
            args.extend(["--effort", reasoning_effort])

        result = await self._runner(args, self.timeout_seconds, prompt)
        if result.exit_code != 0:
            classified = self._classify_command_failure(
                provider=LLM_PROVIDER_CLAUDE,
                stderr=result.stderr,
                stdout=result.stdout,
                exit_code=result.exit_code,
            )
            self._capture_provider_response(
                provider=LLM_PROVIDER_CLAUDE,
                source_title=source_title,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                raw_output=result.stdout,
                parse_error_code=classified.code,
                parse_error_message=str(classified),
            )
            raise classified

        return self._parse_and_capture_provider_output(
            provider=LLM_PROVIDER_CLAUDE,
            source_title=source_title,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            raw_output=result.stdout,
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
            self._schema_error_code(LLM_PROVIDER_GEMINI),
            "Gemini CLI strict output schema enforcement is not available",
            provider=LLM_PROVIDER_GEMINI,
            retryable=False,
        )

    def _parse_and_capture_provider_output(
        self,
        *,
        provider: str,
        source_title: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        raw_output: str,
    ) -> dict[str, str]:
        try:
            article = self._parse_provider_output(provider, raw_output)
        except LlmClientError as exc:
            self._capture_provider_response(
                provider=provider,
                source_title=source_title,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                raw_output=raw_output,
                parse_error_code=exc.code,
                parse_error_message=str(exc),
            )
            raise

        self._capture_provider_response(
            provider=provider,
            source_title=source_title,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            raw_output=raw_output,
            article=article,
        )
        return article

    def _capture_provider_response(
        self,
        *,
        provider: str,
        source_title: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        raw_output: str,
        parse_error_code: str | None = None,
        parse_error_message: str | None = None,
        article: Mapping[str, str] | None = None,
    ) -> None:
        capture_dir = self._response_capture_dir
        if capture_dir is None:
            return
        try:
            capture_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now(UTC).strftime("%Y%m%d")
            capture_file = capture_dir / f"llm_response_{day}.jsonl"
            stdout_text, stdout_truncated, stdout_chars = self._capture_text(stdout)
            stderr_text, stderr_truncated, stderr_chars = self._capture_text(stderr)
            raw_text, raw_truncated, raw_chars = self._capture_text(raw_output)
            include_content = self._capture_full_response_content
            payload: dict[str, Any] = {
                "id": str(uuid4()),
                "captured_at": datetime.now(UTC).isoformat(),
                "provider": provider,
                "source_title": source_title,
                "exit_code": int(exit_code),
                "parse": {
                    "ok": parse_error_code is None,
                    "error_code": parse_error_code or "",
                    "error_message": parse_error_message or "",
                },
                "stdout": {
                    "text": stdout_text if include_content else "",
                    "chars": stdout_chars,
                    "truncated": stdout_truncated,
                },
                "stderr": {
                    "text": stderr_text if include_content else "",
                    "chars": stderr_chars,
                    "truncated": stderr_truncated,
                },
                "raw_output": {
                    "text": raw_text if include_content else "",
                    "chars": raw_chars,
                    "truncated": raw_truncated,
                },
            }
            if article is not None:
                payload["article"] = (
                    {
                        "title": str(article.get("title") or ""),
                        "lead": str(article.get("lead") or ""),
                        "body": str(article.get("body") or ""),
                        "fact_box": str(article.get("fact_box") or "{}"),
                        "timestamps": str(article.get("timestamps") or "[]"),
                    }
                    if include_content
                    else {
                        "title_chars": len(str(article.get("title") or "")),
                        "lead_chars": len(str(article.get("lead") or "")),
                        "body_chars": len(str(article.get("body") or "")),
                        "fact_box_chars": len(str(article.get("fact_box") or "{}")),
                        "timestamps_chars": len(str(article.get("timestamps") or "[]")),
                    }
                )
            with capture_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False))
                handle.write("\n")
        except Exception:
            # Capture must never break LLM pipeline flow.
            return

    def _capture_text(self, value: str) -> tuple[str, bool, int]:
        text = str(value or "")
        chars = len(text)
        max_chars = self._response_capture_max_chars
        if chars <= max_chars:
            return text, False, chars
        return text[:max_chars], True, chars

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
        if self._looks_like_schema_mismatch(message):
            return LlmClientError(
                self._schema_error_code(provider),
                message,
                provider=provider,
                retryable=False,
            )
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
            pass

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
        required = set(ARTICLE_FIELD_KEYS)
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

    def _looks_like_schema_mismatch(self, text: str) -> bool:
        lowered = text.lower()
        return (
            "invalid_json_schema" in lowered
            or ("response_format" in lowered and "schema" in lowered)
            or "text.format.schema" in lowered
        )

    def _schema_error_code(self, provider: str) -> str:
        normalized = normalize_llm_provider(provider, allow_none=False)
        return f"llm_provider_schema_invalid_{normalized}"

    def _provider_schema_compact(self, provider: str) -> str:
        schema = self._provider_schema(provider)
        return json.dumps(schema, ensure_ascii=True, separators=(",", ":"))

    def _provider_schema(self, provider: str) -> dict[str, Any]:
        del provider  # schema is currently provider-agnostic; signature kept for future divergence
        required = list(ARTICLE_FIELD_KEYS)
        return {
            "type": "object",
            "properties": {key: {"type": "string"} for key in ARTICLE_FIELD_KEYS},
            "required": required,
            "additionalProperties": False,
        }

    def validate_provider_schema(self, provider: str) -> None:
        normalized = normalize_llm_provider(provider, allow_none=False)
        if normalized == LLM_PROVIDER_GEMINI:
            raise LlmClientError(
                self._schema_error_code(normalized),
                "Gemini CLI does not support strict output schema enforcement",
                provider=normalized,
                retryable=False,
            )
        schema = self._provider_schema(normalized)
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict):
            raise LlmClientError(
                self._schema_error_code(normalized),
                "LLM output schema is invalid: properties must be object",
                provider=normalized,
                retryable=False,
            )
        property_keys = {str(key) for key in properties.keys()}
        required_keys = {str(item) for item in required} if isinstance(required, list) else set()
        missing_core = set(ARTICLE_CORE_KEYS) - property_keys
        if missing_core:
            raise LlmClientError(
                self._schema_error_code(normalized),
                f"LLM output schema is invalid: missing core properties={sorted(missing_core)}",
                provider=normalized,
                retryable=False,
            )
        if normalized == LLM_PROVIDER_CODEX:
            missing_required = property_keys - required_keys
            if missing_required:
                raise LlmClientError(
                    self._schema_error_code(normalized),
                    (
                        "LLM output schema is invalid for codex: "
                        f"required must include all properties, missing={sorted(missing_required)}"
                    ),
                    provider=normalized,
                    retryable=False,
                )
        missing_field_keys = set(ARTICLE_FIELD_KEYS) - property_keys
        if missing_field_keys:
            raise LlmClientError(
                self._schema_error_code(normalized),
                f"LLM output schema is invalid: missing properties={sorted(missing_field_keys)}",
                provider=normalized,
                retryable=False,
            )

    def _trim_error_message(self, text: str, limit: int = 600) -> str:
        trimmed = str(text or "").strip()
        if len(trimmed) <= limit:
            return trimmed
        return trimmed[:limit]
