from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app import llm_policy as _llm_policy
from app.services.llm_errors import LlmClientError

LLM_GROK_MAX_TURNS = 15
# `--tools ""` is ignored, so the default tool set stays available. Deny it
# explicitly. Article jobs also use a temp `--cwd` so the repo AGENTS.md is
# not injected into the prompt.
LLM_GROK_DISABLED_TOOLS: tuple[str, ...] = (
    "Agent",
    "ask_user_question",
    "enter_plan_mode",
    "exit_plan_mode",
    "get_command_or_subagent_output",
    "grep",
    "image_edit",
    "image_gen",
    "image_to_video",
    "kill_command_or_subagent",
    "list_dir",
    "memory_search",
    "monitor",
    "read_file",
    "reference_to_video",
    "run_terminal_cmd",
    "run_terminal_command",
    "scheduler_create",
    "scheduler_delete",
    "scheduler_list",
    "search_replace",
    "search_tool",
    "send_feedback",
    "spawn_subagent",
    "todo_write",
    "use_tool",
    "web_fetch",
    "web_search",
    "workflow",
    "write",
)
# cursor-agent has no output-schema flag, so the contract is appended to the prompt
# and the app-side article validation is the enforcement point.
LLM_CURSOR_OUTPUT_CONTRACT_TEMPLATE = """
Output contract (mandatory):
- Respond with exactly one JSON object and nothing else: no markdown code fences, no prose before or after.
- The object must match this JSON Schema: {schema_json}
- Every value must be a JSON string. fact_box and timestamps are Markdown bullet lists inside a string, not arrays or objects.
- Do not use any tools. Do not read or write files.
""".strip()
# Ask mode still allows file reads outside --workspace and web search, and a
# project .cursor/cli.json is not honored. A per-call CURSOR_CONFIG_DIR with
# these deny rules blocks them while keeping the login.
LLM_CURSOR_DENIED_PERMISSIONS: tuple[str, ...] = (
    "Shell(*)",
    "Read(**)",
    "Read(/**)",
    "Write(**)",
    "Write(/**)",
    "WebFetch(*)",
    "Mcp(*:*)",
)
LLM_CODEX_MODEL_DEFAULT = _llm_policy.LLM_CODEX_MODEL_DEFAULT
LLM_PROVIDER_CODEX = _llm_policy.LLM_PROVIDER_CODEX
LLM_PROVIDER_GROK = _llm_policy.LLM_PROVIDER_GROK
LLM_PROVIDER_CURSOR = _llm_policy.LLM_PROVIDER_CURSOR
LLM_CODEX_REASONING_EFFORT_OPTIONS = _llm_policy.LLM_CODEX_REASONING_EFFORT_OPTIONS
LLM_GROK_REASONING_EFFORT_OPTIONS = _llm_policy.LLM_GROK_REASONING_EFFORT_OPTIONS
normalize_codex_model = _llm_policy.normalize_codex_model
normalize_grok_model = _llm_policy.normalize_grok_model
normalize_cursor_model = _llm_policy.normalize_cursor_model
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


class CommandRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        timeout_seconds: int,
        stdin_text: str | None,
        /,
        *,
        env: Mapping[str, str] | None = None,
    ) -> Awaitable[CommandExecutionResult]: ...


CommandExists = Callable[[str], bool]


def _subprocess_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


async def _kill_timed_out_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError:
            process.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


async def default_command_runner(
    args: list[str],
    timeout_seconds: int,
    stdin_text: str | None,
    *,
    env: Mapping[str, str] | None = None,
) -> CommandExecutionResult:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **env} if env else None,
        **_subprocess_group_kwargs(),
    )
    try:
        input_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=input_bytes),
            timeout=max(1, int(timeout_seconds)),
        )
    except TimeoutError as exc:
        await _kill_timed_out_process(process)
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
    normalized = normalize_llm_provider(provider, allow_none=False)
    if normalized == LLM_PROVIDER_CODEX:
        return "codex"
    if normalized == LLM_PROVIDER_GROK:
        return "grok"
    if normalized == LLM_PROVIDER_CURSOR:
        return "cursor-agent"
    raise LlmClientError(
        "llm_provider_invalid",
        f"Unsupported provider: {provider}",
        provider=provider,
        retryable=False,
    )


def provider_command_candidates(provider: str) -> tuple[str, ...]:
    command = provider_command_name(provider)
    if os.name != "nt":
        return (command,)
    return (f"{command}.cmd", command)


def resolve_provider_command(
    provider: str,
    *,
    command_exists: CommandExists,
) -> str:
    candidates = provider_command_candidates(provider)
    for command in candidates:
        if command_exists(command):
            return command
    return candidates[0]


def _ensure_provider_command(
    provider: str,
    *,
    command_exists: CommandExists,
) -> str:
    command = resolve_provider_command(provider, command_exists=command_exists)
    if command_exists(command):
        return command
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
    command = _ensure_provider_command(LLM_PROVIDER_CODEX, command_exists=command_exists)

    with tempfile.TemporaryDirectory(prefix="brieftube-llm-codex-") as tmpdir:
        schema_file = Path(tmpdir) / "article.schema.json"
        output_file = Path(tmpdir) / "last_message.json"
        schema_file.write_text(schema_json, encoding="utf-8")

        args = [
            command,
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
        effort = str(reasoning_effort or "").strip().lower()
        if effort in LLM_CODEX_REASONING_EFFORT_OPTIONS:
            args.extend(["-c", f'model_reasoning_effort="{effort}"'])

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


async def run_grok_provider_command(
    *,
    prompt: str,
    model: str,
    reasoning_effort: str,
    schema_json: str,
    timeout_seconds: int,
    runner: CommandRunner,
    command_exists: CommandExists,
) -> ProviderCommandResult:
    command = _ensure_provider_command(LLM_PROVIDER_GROK, command_exists=command_exists)

    with tempfile.TemporaryDirectory(prefix="brieftube-llm-grok-") as tmpdir:
        prompt_file = Path(tmpdir) / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        args = [
            command,
            "--prompt-file",
            str(prompt_file),
            "--cwd",
            tmpdir,
            "--json-schema",
            schema_json,
            "-m",
            normalize_grok_model(model),
            "--max-turns",
            str(LLM_GROK_MAX_TURNS),
            "--disallowed-tools",
            ",".join(LLM_GROK_DISABLED_TOOLS),
            "--disable-web-search",
            "--no-subagents",
            "--no-memory",
            "--no-ask-user",
            "--output-format",
            "json",
        ]
        effort = str(reasoning_effort or "").strip().lower()
        if effort in LLM_GROK_REASONING_EFFORT_OPTIONS:
            args.extend(["--reasoning-effort", effort])

        result = await runner(args, timeout_seconds, None)
        raw_output = result.stdout.strip()

    return ProviderCommandResult(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        raw_output=raw_output,
    )


async def run_cursor_provider_command(
    *,
    prompt: str,
    model: str,
    schema_json: str,
    timeout_seconds: int,
    runner: CommandRunner,
    command_exists: CommandExists,
) -> ProviderCommandResult:
    command = _ensure_provider_command(LLM_PROVIDER_CURSOR, command_exists=command_exists)
    contract = LLM_CURSOR_OUTPUT_CONTRACT_TEMPLATE.format(schema_json=schema_json)

    # Ask mode in an empty temp workspace keeps repo AGENTS.md out of the prompt;
    # the isolated config dir denies tools. The prompt goes through stdin to
    # avoid argv limits.
    with tempfile.TemporaryDirectory(prefix="brieftube-llm-cursor-") as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        config_dir = Path(tmpdir) / "config"
        workspace.mkdir()
        config_dir.mkdir()
        (config_dir / "cli-config.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "editor": {"vimMode": False},
                    "permissions": {"allow": [], "deny": list(LLM_CURSOR_DENIED_PERMISSIONS)},
                }
            ),
            encoding="utf-8",
        )
        args = [
            command,
            "--print",
            "--output-format",
            "json",
            "--mode",
            "ask",
            "--trust",
            "--workspace",
            str(workspace),
            "--model",
            normalize_cursor_model(model),
        ]
        result = await runner(
            args,
            timeout_seconds,
            f"{prompt}\n\n{contract}",
            env={"CURSOR_CONFIG_DIR": str(config_dir)},
        )
        raw_output = result.stdout.strip()

    return ProviderCommandResult(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        raw_output=raw_output,
    )
