from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app import llm_policy as _llm_policy
from app.services.llm_errors import LlmClientError

LLM_CODEX_MODEL_DEFAULT = _llm_policy.LLM_CODEX_MODEL_DEFAULT
LLM_PROVIDER_CLAUDE = _llm_policy.LLM_PROVIDER_CLAUDE
LLM_PROVIDER_CODEX = _llm_policy.LLM_PROVIDER_CODEX
LLM_PROVIDER_GEMINI = _llm_policy.LLM_PROVIDER_GEMINI
normalize_codex_model = _llm_policy.normalize_codex_model
normalize_llm_provider = _llm_policy.normalize_llm_provider


@dataclass(slots=True)
class CommandExecutionResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class ProviderCommandResult:
    exit_code: int
    stdout: str
    stderr: str
    raw_output: str


CommandRunner = Callable[[list[str], int, str | None], Awaitable[CommandExecutionResult]]
CommandExists = Callable[[str], bool]


async def default_command_runner(
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


def default_command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def provider_command_name(provider: str) -> str:
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


def _ensure_provider_command(
    provider: str,
    *,
    command_exists: CommandExists,
) -> None:
    command = provider_command_name(provider)
    if command_exists(command):
        return
    raise LlmClientError(
        f"llm_provider_unavailable_{provider}",
        f"LLM provider command is not available: {command}",
        provider=provider,
        retryable=False,
    )


async def run_codex_provider_command(
    *,
    prompt: str,
    model: str,
    reasoning_effort: str,
    schema_json: str,
    timeout_seconds: int,
    runner: CommandRunner,
    command_exists: CommandExists,
) -> ProviderCommandResult:
    _ensure_provider_command(LLM_PROVIDER_CODEX, command_exists=command_exists)

    with tempfile.TemporaryDirectory(prefix="brieftube-llm-codex-") as tmpdir:
        schema_file = Path(tmpdir) / "article.schema.json"
        output_file = Path(tmpdir) / "last_message.json"
        schema_file.write_text(schema_json, encoding="utf-8")

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

        result = await runner(args, timeout_seconds, prompt)
        raw_output = ""
        if output_file.exists():
            raw_output = output_file.read_text(encoding="utf-8", errors="replace").strip()
        if not raw_output:
            raw_output = result.stdout

    return ProviderCommandResult(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        raw_output=raw_output,
    )


async def run_claude_provider_command(
    *,
    prompt: str,
    model: str,
    reasoning_effort: str,
    schema_json: str,
    timeout_seconds: int,
    runner: CommandRunner,
    command_exists: CommandExists,
) -> ProviderCommandResult:
    _ensure_provider_command(LLM_PROVIDER_CLAUDE, command_exists=command_exists)

    args = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        schema_json,
        "--no-session-persistence",
    ]
    if model:
        args.extend(["--model", model])
    if reasoning_effort:
        args.extend(["--effort", reasoning_effort])

    result = await runner(args, timeout_seconds, prompt)
    return ProviderCommandResult(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        raw_output=result.stdout,
    )
