from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.services.llm import CommandExecutionResult, LlmClientError, UnifiedLlmClient


def test_runtime_not_ready_when_prompt_is_empty() -> None:
    client = UnifiedLlmClient(timeout_seconds=10)
    reason = client.runtime_not_ready_reason(
        {
            "provider_primary": "codex",
            "provider_fallback": "claude",
            "prompt_template": "",
        }
    )
    assert reason == "llm_prompt_missing"


def test_runtime_plan_allows_primary_when_fallback_missing() -> None:
    client = UnifiedLlmClient(
        timeout_seconds=10,
        command_exists=lambda name: name == "codex",
    )
    plan = client.resolve_runtime_plan(
        {
            "provider_primary": "codex",
            "provider_fallback": "claude",
            "prompt_template": "{transcript_text}",
        }
    )
    assert plan.blocking_reason is None
    assert plan.providers_to_try == ["codex"]
    assert plan.warnings == ["llm_provider_unavailable_claude"]


def test_restructure_codex_success_uses_stdin_and_output_file() -> None:
    async def fake_runner(args: list[str], timeout: int, stdin_text: str | None) -> CommandExecutionResult:
        assert args[0] == "codex"
        assert args[-1] == "-"
        assert stdin_text == "Title=Source\nBody=Transcript"

        schema_path = Path(args[args.index("--output-schema") + 1])
        output_path = Path(args[args.index("--output-last-message") + 1])
        assert schema_path.exists()

        payload = {
            "title": "Article title",
            "lead": "Lead",
            "body": "Body",
            "fact_box": "{}",
            "timestamps": "[]",
        }
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        return CommandExecutionResult(exit_code=0, stdout="codex logs", stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "codex",
                "provider_fallback": "none",
                "prompt_template": "Title={source_title}\nBody={transcript_text}",
            },
        )
    )

    assert article["title"] == "Article title"
    assert article["lead"] == "Lead"
    assert article["body"] == "Body"


def test_restructure_fallbacks_to_claude_when_codex_refuses() -> None:
    calls: list[str] = []

    async def fake_runner(args: list[str], timeout: int, stdin_text: str | None) -> CommandExecutionResult:
        calls.append(args[0])
        if args[0] == "codex":
            output_path = Path(args[args.index("--output-last-message") + 1])
            output_path.write_text(
                json.dumps({"result": "잠재적인 프롬프트 인젝션 시도를 감지했습니다."}),
                encoding="utf-8",
            )
            return CommandExecutionResult(exit_code=0, stdout="", stderr="")

        payload = {
            "type": "result",
            "is_error": False,
            "structured_output": {
                "title": "Claude title",
                "lead": "Claude lead",
                "body": "Claude body",
                "fact_box": "{}",
                "timestamps": "[]",
            },
        }
        return CommandExecutionResult(exit_code=0, stdout=json.dumps(payload), stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "codex",
                "provider_fallback": "claude",
                "prompt_template": "{source_title}\n{transcript_text}",
            },
        )
    )

    assert article["title"] == "Claude title"
    assert calls[:3] == ["codex", "codex", "claude"]


def test_restructure_raises_auth_required_when_provider_not_logged_in() -> None:
    async def fake_runner(args: list[str], timeout: int, stdin_text: str | None) -> CommandExecutionResult:
        return CommandExecutionResult(exit_code=1, stdout="", stderr="Not logged in. Please login first.")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    try:
        asyncio.run(
            client.restructure(
                source_title="Source",
                transcript_text="Transcript",
                settings={
                    "provider_primary": "codex",
                    "provider_fallback": "none",
                    "prompt_template": "{source_title}\n{transcript_text}",
                },
            )
        )
        assert False, "expected LlmClientError"
    except LlmClientError as exc:
        assert exc.code == "llm_provider_auth_required"
