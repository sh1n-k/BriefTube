from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.llm_policy import (
    LLM_CODEX_MODEL_OPTIONS,
    LLM_CODEX_REASONING_EFFORT_OPTIONS,
    LLM_PROVIDER_CODEX,
)
from app.services.llm_invocation import resolve_provider_command


@dataclass(frozen=True, slots=True)
class CodexModelCapability:
    value: str
    label: str
    default_reasoning_effort: str
    reasoning_efforts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodexCapabilityResult:
    available: bool
    source: str
    error: str
    models: tuple[CodexModelCapability, ...]
    reasoning_efforts: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "source": self.source,
            "error": self.error,
            "models": [
                {
                    "value": model.value,
                    "label": model.label,
                    "default_reasoning_effort": model.default_reasoning_effort,
                    "reasoning_efforts": list(model.reasoning_efforts),
                }
                for model in self.models
            ],
            "reasoning_efforts": list(self.reasoning_efforts),
        }


CommandExists = Callable[[str], bool]
CommandRunner = Callable[[list[str], int], Awaitable[tuple[int, str, str]]]


class LlmCapabilityProbe:
    def __init__(
        self,
        *,
        timeout_seconds: int = 5,
        ttl_seconds: int = 600,
        command_exists: CommandExists | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._command_exists = command_exists or (lambda name: shutil.which(name) is not None)
        self._runner = runner or _run_command
        self._codex_cache: tuple[float, CodexCapabilityResult] | None = None

    async def get_codex_capabilities(self, *, refresh: bool = False) -> CodexCapabilityResult:
        now = time.monotonic()
        if not refresh and self._codex_cache is not None:
            expires_at, cached = self._codex_cache
            if expires_at > now:
                return cached

        result = await self._probe_codex()
        self._codex_cache = (now + self.ttl_seconds, result)
        return result

    async def _probe_codex(self) -> CodexCapabilityResult:
        command = resolve_provider_command(LLM_PROVIDER_CODEX, command_exists=self._command_exists)
        if not self._command_exists(command):
            return _fallback_codex_result(source="fallback", error="codex command not found")

        first_error = ""
        for args, source in (
            ([command, "debug", "models"], "codex-debug-models"),
            ([command, "debug", "models", "--bundled"], "codex-debug-models-bundled"),
        ):
            try:
                exit_code, stdout, _stderr = await self._runner(args, self.timeout_seconds)
            except TimeoutError:
                error = f"{source} timed out"
            except OSError as exc:
                error = f"{source} failed: {exc}"
            else:
                if exit_code == 0:
                    try:
                        return _parse_codex_models(stdout, source=source)
                    except ValueError as exc:
                        error = f"{source} returned invalid model catalog: {exc}"
                else:
                    error = f"{source} exited {exit_code}"
            if not first_error:
                first_error = error

        return _fallback_codex_result(source="fallback", error=first_error)


async def resolve_codex_capabilities(runtime: Any, *, refresh: bool = False) -> CodexCapabilityResult:
    probe = getattr(runtime, "llm_capability_probe", None)
    if not isinstance(probe, LlmCapabilityProbe):
        probe = LlmCapabilityProbe(command_exists=lambda _name: False)
    return await probe.get_codex_capabilities(refresh=refresh)


async def _run_command(args: list[str], timeout_seconds: int) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=max(1, int(timeout_seconds)),
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _parse_codex_models(raw: str, *, source: str) -> CodexCapabilityResult:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
    models_payload = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models_payload, list):
        raise ValueError("models must be a list")

    models: list[CodexModelCapability] = []
    effort_values: set[str] = set()
    for item in models_payload:
        if not isinstance(item, dict):
            continue
        visibility = str(item.get("visibility") or "list").strip().lower()
        if visibility not in {"", "list"}:
            continue
        slug = str(item.get("slug") or "").strip().lower()
        if not slug:
            continue
        label = str(item.get("display_name") or slug).strip() or slug
        default_effort = str(item.get("default_reasoning_level") or "").strip().lower()
        supported = _parse_reasoning_efforts(item.get("supported_reasoning_levels"))
        if default_effort and default_effort not in supported:
            supported = (*supported, default_effort)
        effort_values.update(supported)
        models.append(
            CodexModelCapability(
                value=slug,
                label=label,
                default_reasoning_effort=default_effort,
                reasoning_efforts=supported,
            )
        )

    if not models:
        raise ValueError("no visible models found")

    return CodexCapabilityResult(
        available=True,
        source=source,
        error="",
        models=tuple(models),
        reasoning_efforts=_sort_efforts(effort_values),
    )


def _parse_reasoning_efforts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    efforts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        effort = str(item.get("effort") or "").strip().lower()
        if effort and effort not in efforts:
            efforts.append(effort)
    return tuple(efforts)


def _fallback_codex_result(*, source: str, error: str) -> CodexCapabilityResult:
    models = tuple(
        CodexModelCapability(
            value=value,
            label=label,
            default_reasoning_effort="",
            reasoning_efforts=(),
        )
        for value, label in LLM_CODEX_MODEL_OPTIONS
    )
    return CodexCapabilityResult(
        available=False,
        source=source,
        error=error,
        models=models,
        reasoning_efforts=_sort_efforts(LLM_CODEX_REASONING_EFFORT_OPTIONS),
    )


def _sort_efforts(values: set[str] | frozenset[str]) -> tuple[str, ...]:
    order = ("low", "medium", "high", "xhigh", "max")
    normalized = {str(value).strip().lower() for value in values if str(value).strip()}
    ordered = [value for value in order if value in normalized]
    ordered.extend(sorted(normalized.difference(order)))
    return tuple(ordered)
