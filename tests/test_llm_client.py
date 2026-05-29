from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.services.llm import (
    LLM_CODEX_MODEL_DEFAULT,
    CommandExecutionResult,
    LlmClientError,
    UnifiedLlmClient,
)
from app.services.llm_errors import classify_command_failure


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
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == "codex"
        assert args[-1] == "-"
        assert args[args.index("-m") + 1] == LLM_CODEX_MODEL_DEFAULT
        assert "-c" not in args
        assert stdin_text == "Title=Source\nBody=Transcript"

        schema_path = Path(args[args.index("--output-schema") + 1])
        output_path = Path(args[args.index("--output-last-message") + 1])
        assert schema_path.exists()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert set(schema["properties"].keys()) == {
            "title",
            "lead",
            "body",
            "fact_box",
            "timestamps",
        }
        assert set(schema["required"]) == set(schema["properties"].keys())

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
    assert article["_llm_provider"] == "codex"
    assert article["_llm_model"] == LLM_CODEX_MODEL_DEFAULT
    assert article["_llm_reasoning_effort"] == ""
    assert article["_llm_generated_at"]


def test_restructure_codex_uses_selected_model_from_settings() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == "codex"
        assert args[args.index("-m") + 1] == "gpt-5.4"
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "title": "Article title",
                    "lead": "Lead",
                    "body": "Body",
                    "fact_box": "{}",
                    "timestamps": "[]",
                }
            ),
            encoding="utf-8",
        )
        return CommandExecutionResult(exit_code=0, stdout="", stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "codex",
                "provider_fallback": "none",
                "prompt_template": "{transcript_text}",
                "llm_model": {"codex": "gpt-5.4"},
            },
        )
    )

    assert article["_llm_model"] == "gpt-5.4"


def test_restructure_codex_preserves_dynamic_model_from_settings() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == "codex"
        assert args[args.index("-m") + 1] == "gpt-5.5"
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "title": "Article title",
                    "lead": "Lead",
                    "body": "Body",
                    "fact_box": "{}",
                    "timestamps": "[]",
                }
            ),
            encoding="utf-8",
        )
        return CommandExecutionResult(exit_code=0, stdout="", stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "codex",
                "provider_fallback": "none",
                "prompt_template": "{transcript_text}",
                "llm_model": {"codex": "gpt-5.5"},
            },
        )
    )

    assert article["_llm_model"] == "gpt-5.5"


def test_restructure_fallbacks_to_claude_when_codex_refuses() -> None:
    calls: list[str] = []

    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
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
    assert article["_llm_provider"] == "claude"


def test_restructure_applies_reasoning_effort_for_codex() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == "codex"
        assert "-c" in args
        assert args[args.index("-c") + 1] == 'model_reasoning_effort="low"'
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "title": "Codex title",
                    "lead": "Codex lead",
                    "body": "Codex body",
                    "fact_box": "{}",
                    "timestamps": "[]",
                }
            ),
            encoding="utf-8",
        )
        return CommandExecutionResult(exit_code=0, stdout="", stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "codex",
                "provider_fallback": "none",
                "prompt_template": "{transcript_text}",
                "llm_reasoning_effort": {"codex": "low"},
            },
        )
    )
    assert article["title"] == "Codex title"


def test_restructure_applies_model_and_effort_for_claude() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == "claude"
        assert args[args.index("--model") + 1] == "sonnet"
        assert args[args.index("--effort") + 1] == "high"
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
                "provider_primary": "claude",
                "provider_fallback": "none",
                "prompt_template": "{transcript_text}",
                "llm_model": {"claude": "sonnet"},
                "llm_reasoning_effort": {"claude": "high"},
            },
        )
    )
    assert article["title"] == "Claude title"
    assert article["_llm_model"] == "sonnet"
    assert article["_llm_reasoning_effort"] == "high"


def test_restructure_raises_auth_required_when_provider_not_logged_in() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        return CommandExecutionResult(
            exit_code=1, stdout="", stderr="Not logged in. Please login first."
        )

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


def test_classify_command_failure_reports_generic_retryable_failure() -> None:
    exc = classify_command_failure(
        provider="codex",
        stderr="temporary command failure",
        stdout="",
        exit_code=2,
    )

    assert exc.code == "llm_provider_command_failed"
    assert exc.provider == "codex"
    assert exc.retryable is True
    assert "temporary command failure" in str(exc)


def test_classify_command_failure_reports_refusal_as_non_retryable() -> None:
    exc = classify_command_failure(
        provider="claude",
        stderr="",
        stdout="cannot comply with this prompt injection request",
        exit_code=1,
    )

    assert exc.code == "llm_provider_refused"
    assert exc.provider == "claude"
    assert exc.retryable is False


def test_runtime_plan_blocks_when_codex_schema_contract_is_invalid() -> None:
    client = UnifiedLlmClient(timeout_seconds=10, command_exists=lambda _: True)

    def fake_schema(_provider: str):
        return {
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

    client._provider_schema = fake_schema  # type: ignore[method-assign]
    reason = client.runtime_not_ready_reason(
        {
            "provider_primary": "codex",
            "provider_fallback": "none",
            "prompt_template": "Body={transcript_text}",
        }
    )
    assert reason == "llm_provider_schema_invalid_codex"


def test_restructure_classifies_invalid_json_schema_as_non_retryable_schema_error() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == "codex"
        return CommandExecutionResult(
            exit_code=1,
            stdout="",
            stderr='{"error":{"code":"invalid_json_schema","param":"text.format.schema"}}',
        )

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    try:
        asyncio.run(
            client.restructure(
                source_title="Source",
                transcript_text="Transcript",
                settings={
                    "provider_primary": "codex",
                    "provider_fallback": "none",
                    "prompt_template": "{transcript_text}",
                },
            )
        )
        assert False, "expected LlmClientError"
    except LlmClientError as exc:
        assert exc.code == "llm_provider_schema_invalid_codex"
        assert exc.retryable is False


def test_restructure_rejects_claude_result_string_with_embedded_article_json() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == "claude"
        payload = {
            "type": "result",
            "is_error": False,
            "result": json.dumps(
                {
                    "title": "From nested string",
                    "lead": "Lead text",
                    "body": "Body text",
                    "fact_box": "{}",
                    "timestamps": "[]",
                }
            ),
        }
        return CommandExecutionResult(exit_code=0, stdout=json.dumps(payload), stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    try:
        asyncio.run(
            client.restructure(
                source_title="Source",
                transcript_text="Transcript",
                settings={
                    "provider_primary": "claude",
                    "provider_fallback": "none",
                    "prompt_template": "{transcript_text}",
                },
            )
        )
        assert False, "expected LlmClientError"
    except LlmClientError as exc:
        assert exc.code == "llm_schema_invalid"


def test_restructure_allows_refusal_word_inside_valid_article_json() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == "codex"
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "title": "Article title",
                    "lead": "음주 측정 거부 사례를 다룬 기사입니다.",
                    "body": "본문에도 거부라는 단어가 자연어 맥락으로 등장할 수 있습니다.",
                    "fact_box": "{}",
                    "timestamps": "[]",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return CommandExecutionResult(exit_code=0, stdout="", stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "codex",
                "provider_fallback": "none",
                "prompt_template": "{transcript_text}",
            },
        )
    )

    assert article["lead"] == "음주 측정 거부 사례를 다룬 기사입니다."


def test_parse_non_json_refusal_text_still_reports_provider_refusal() -> None:
    client = UnifiedLlmClient(timeout_seconds=10, command_exists=lambda _: True)
    try:
        client._parse_provider_output("codex", "요청을 거부합니다.")
        assert False, "expected LlmClientError"
    except LlmClientError as exc:
        assert exc.code == "llm_provider_refused"
        assert exc.retryable is False


def test_restructure_fallbacks_to_codex_when_claude_refuses_twice() -> None:
    calls: list[str] = []

    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        calls.append(args[0])
        if args[0] == "claude":
            refused = {
                "type": "result",
                "subtype": "refusal",
                "is_error": True,
                "result": "잠재적인 프롬프트 인젝션 시도를 감지했습니다.",
            }
            return CommandExecutionResult(exit_code=0, stdout=json.dumps(refused), stderr="")

        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "title": "Codex fallback title",
                    "lead": "Codex fallback lead",
                    "body": "Codex fallback body",
                    "fact_box": "{}",
                    "timestamps": "[]",
                }
            ),
            encoding="utf-8",
        )
        return CommandExecutionResult(exit_code=0, stdout="", stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "claude",
                "provider_fallback": "codex",
                "prompt_template": "{source_title}\n{transcript_text}",
            },
        )
    )

    assert article["title"] == "Codex fallback title"
    assert calls[:3] == ["claude", "claude", "codex"]


def test_runtime_plan_blocks_when_primary_is_gemini_without_schema_enforcement() -> None:
    client = UnifiedLlmClient(timeout_seconds=10, command_exists=lambda _: True)
    reason = client.runtime_not_ready_reason(
        {
            "provider_primary": "gemini",
            "provider_fallback": "none",
            "prompt_template": "{transcript_text}",
            "llm_model": {"gemini": "gemini-3.1-pro-preview"},
            "llm_reasoning_effort": {"gemini": "none"},
        }
    )
    assert reason == "llm_provider_schema_invalid_gemini"


def test_runtime_plan_keeps_primary_and_warns_when_fallback_is_gemini() -> None:
    client = UnifiedLlmClient(timeout_seconds=10, command_exists=lambda _: True)
    plan = client.resolve_runtime_plan(
        {
            "provider_primary": "codex",
            "provider_fallback": "gemini",
            "prompt_template": "{transcript_text}",
        }
    )
    assert plan.blocking_reason is None
    assert plan.providers_to_try == ["codex"]
    assert plan.warnings == ["llm_provider_schema_invalid_gemini"]


def test_response_capture_redacts_content_by_default(tmp_path) -> None:
    capture_dir = tmp_path / "llm-capture"

    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "title": "Sensitive title",
                    "lead": "Sensitive lead",
                    "body": "Sensitive body",
                    "fact_box": '{"secret":"value"}',
                    "timestamps": '["00:00"]',
                }
            ),
            encoding="utf-8",
        )
        return CommandExecutionResult(exit_code=0, stdout="stdout body", stderr="stderr body")

    client = UnifiedLlmClient(
        timeout_seconds=10,
        runner=fake_runner,
        command_exists=lambda _: True,
        response_capture_dir=str(capture_dir),
    )
    asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "codex",
                "provider_fallback": "none",
                "prompt_template": "{transcript_text}",
            },
        )
    )

    capture_files = list(capture_dir.glob("llm_response_*.jsonl"))
    assert len(capture_files) == 1
    payload = json.loads(capture_files[0].read_text(encoding="utf-8").strip())
    assert payload["stdout"]["text"] == ""
    assert payload["stderr"]["text"] == ""
    assert payload["raw_output"]["text"] == ""
    assert payload["stdout"]["chars"] == len("stdout body")
    assert payload["raw_output"]["chars"] > 0
    assert payload["article"] == {
        "title_chars": len("Sensitive title"),
        "lead_chars": len("Sensitive lead"),
        "body_chars": len("Sensitive body"),
        "fact_box_chars": len('{"secret":"value"}'),
        "timestamps_chars": len('["00:00"]'),
    }
