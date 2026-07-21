from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from app.services import llm_invocation
from app.services.llm import (
    LLM_CODEX_MODEL_DEFAULT,
    LLM_GROK_MODEL_DEFAULT,
    CommandExecutionResult,
    LlmClientError,
    UnifiedLlmClient,
)
from app.services.llm_errors import classify_command_failure
from app.services.llm_invocation import default_command_runner, resolve_provider_command


def _expected_provider_command(provider: str) -> str:
    return f"{provider}.cmd" if sys.platform == "win32" else provider


def test_runtime_not_ready_when_prompt_is_empty() -> None:
    client = UnifiedLlmClient(timeout_seconds=10)
    reason = client.runtime_not_ready_reason(
        {
            "provider_primary": "codex",
            "provider_fallback": "none",
            "prompt_template": "",
        }
    )
    assert reason == "llm_prompt_missing"


def test_classify_command_failure_redacts_provider_output() -> None:
    sentinel = "RAW_PROVIDER_OUTPUT_SECRET"
    error = classify_command_failure(
        provider="codex",
        stdout=f"model echoed {sentinel}",
        stderr="runtime failed",
        exit_code=1,
    )

    assert error.code == "llm_provider_command_failed"
    assert str(error) == "LLM provider command failed (runtime); exit_code=1"
    assert sentinel not in str(error)
    assert error.exit_code == 1
    assert error.stderr_summary == "runtime failed"
    assert sentinel in error.stdout_summary


def test_summarize_provider_stream_masks_secrets_and_limits_length() -> None:
    from app.services.llm_errors import mask_secrets, summarize_provider_stream

    masked = mask_secrets("Authorization: Bearer super-secret-token-value gho_abcdefghijk")
    assert "super-secret-token-value" not in masked
    assert "gho_***" in masked or "gho_" in masked

    long = "word " * 200
    summary = summarize_provider_stream(long, limit=40)
    assert len(summary) <= 40


def test_default_command_runner_times_out() -> None:
    script = "import time; time.sleep(30)"

    with pytest.raises(LlmClientError) as exc_info:
        asyncio.run(default_command_runner([sys.executable, "-c", script], 1, None))

    assert exc_info.value.code == "llm_timeout"


def test_resolve_provider_command_prefers_cmd_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_invocation.os, "name", "nt")

    command = resolve_provider_command(
        "codex",
        command_exists=lambda name: name == "codex.cmd",
    )

    assert command == "codex.cmd"


def test_resolve_provider_command_keeps_plain_command_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_invocation.os, "name", "posix")

    command = resolve_provider_command(
        "codex",
        command_exists=lambda name: name == "codex",
    )

    assert command == "codex"


def test_runtime_plan_normalizes_legacy_provider_values_to_codex_only() -> None:
    client = UnifiedLlmClient(
        timeout_seconds=10,
        command_exists=lambda name: name == "codex",
    )
    plan = client.resolve_runtime_plan(
        {
            "provider_primary": "claude",
            "provider_fallback": "gemini",
            "prompt_template": "{transcript_text}",
        }
    )
    assert plan.blocking_reason is None
    assert plan.providers_to_try == ["codex"]
    assert plan.warnings == []


def test_restructure_codex_success_uses_stdin_and_output_file() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == _expected_provider_command("codex")
        assert args[-1] == "-"
        assert args[args.index("-m") + 1] == LLM_CODEX_MODEL_DEFAULT
        assert "-c" not in args
        assert stdin_text is not None
        assert stdin_text.startswith("Title=Source\nBody=Security and accuracy rules:")
        assert "<untrusted_transcript>\nTranscript\n</untrusted_transcript>" in stdin_text

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


def test_restructure_codex_preserves_dynamic_model_from_settings() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == _expected_provider_command("codex")
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


def test_restructure_applies_reasoning_effort_for_codex() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == _expected_provider_command("codex")
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
    assert str(exc) == "LLM provider command failed (runtime); exit_code=2"


def test_classify_command_failure_reports_refusal_as_non_retryable() -> None:
    exc = classify_command_failure(
        provider="codex",
        stderr="",
        stdout="cannot comply with this prompt injection request",
        exit_code=1,
    )

    assert exc.code == "llm_provider_refused"
    assert exc.provider == "codex"
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
        assert args[0] == _expected_provider_command("codex")
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


def test_restructure_rejects_result_string_with_embedded_article_json() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == _expected_provider_command("codex")
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "result": json.dumps(
                        {
                            "title": "From nested string",
                            "lead": "Lead text",
                            "body": "Body text",
                            "fact_box": "{}",
                            "timestamps": "[]",
                        }
                    )
                }
            ),
            encoding="utf-8",
        )
        return CommandExecutionResult(exit_code=0, stdout="", stderr="")

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
        assert exc.code == "llm_schema_invalid"


def test_restructure_allows_refusal_word_inside_valid_article_json() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == _expected_provider_command("codex")
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


def test_response_capture_includes_streams_on_command_failure(tmp_path) -> None:
    capture_dir = tmp_path / "llm-capture-fail"

    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        return CommandExecutionResult(
            exit_code=1,
            stdout="partial stdout body",
            stderr="grok runtime boom: connection reset",
        )

    client = UnifiedLlmClient(
        timeout_seconds=10,
        runner=fake_runner,
        command_exists=lambda _: True,
        response_capture_dir=str(capture_dir),
        capture_full_response_content=False,
    )
    with pytest.raises(LlmClientError) as exc_info:
        asyncio.run(
            client.restructure(
                source_title="Source",
                transcript_text="Transcript",
                settings={
                    "provider_primary": "grok",
                    "provider_fallback": "none",
                    "prompt_template": "{transcript_text}",
                },
            )
        )

    assert exc_info.value.code == "llm_provider_command_failed"
    assert "connection reset" in exc_info.value.stderr_summary

    capture_files = list(capture_dir.glob("llm_response_*.jsonl"))
    assert len(capture_files) == 1
    payload = json.loads(capture_files[0].read_text(encoding="utf-8").strip())
    assert payload["exit_code"] == 1
    assert "connection reset" in payload["stderr"]["text"]
    assert "partial stdout body" in payload["stdout"]["text"]
    assert payload["raw_output"]["text"] == ""
    assert "article" not in payload


def test_resolve_llm_response_capture_dir_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import _resolve_llm_response_capture_dir

    monkeypatch.delenv("BRIEFTUBE_LLM_RESPONSE_CAPTURE_DIR", raising=False)
    monkeypatch.delenv("BRIEFTUBE_LLM_RESPONSE_CAPTURE_DISABLED", raising=False)
    assert _resolve_llm_response_capture_dir("dev") == "./output/llm_raw"
    assert _resolve_llm_response_capture_dir("prod") == "./logs/prod/llm_raw"

    monkeypatch.setenv("BRIEFTUBE_LLM_RESPONSE_CAPTURE_DISABLED", "1")
    assert _resolve_llm_response_capture_dir("prod") is None


def test_runtime_plan_accepts_grok_when_command_exists() -> None:
    client = UnifiedLlmClient(
        timeout_seconds=10,
        command_exists=lambda name: name == "grok",
    )
    plan = client.resolve_runtime_plan(
        {
            "provider_primary": "grok",
            "provider_fallback": "none",
            "prompt_template": "{transcript_text}",
        }
    )
    assert plan.blocking_reason is None
    assert plan.providers_to_try == ["grok"]


def test_runtime_plan_blocks_when_grok_command_missing() -> None:
    client = UnifiedLlmClient(
        timeout_seconds=10,
        command_exists=lambda name: name == "codex",
    )
    reason = client.runtime_not_ready_reason(
        {
            "provider_primary": "grok",
            "provider_fallback": "none",
            "prompt_template": "{transcript_text}",
        }
    )
    assert reason == "llm_provider_unavailable_grok"


def test_restructure_grok_success_uses_prompt_file_and_structured_output() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == _expected_provider_command("grok")
        assert stdin_text is None
        assert "--prompt-file" in args
        assert "--json-schema" in args
        assert args[args.index("-m") + 1] == LLM_GROK_MODEL_DEFAULT
        assert "--max-turns" in args
        assert args[args.index("--tools") + 1] == ""
        assert "--disable-web-search" in args
        assert "--no-subagents" in args
        assert "--no-memory" in args
        assert args[args.index("--output-format") + 1] == "json"
        assert "--reasoning-effort" not in args

        prompt_path = Path(args[args.index("--prompt-file") + 1])
        assert prompt_path.exists()
        prompt_text = prompt_path.read_text(encoding="utf-8")
        assert "Source" in prompt_text
        assert "Transcript" in prompt_text

        schema_raw = args[args.index("--json-schema") + 1]
        schema = json.loads(schema_raw)
        assert set(schema["properties"].keys()) == {
            "title",
            "lead",
            "body",
            "fact_box",
            "timestamps",
        }

        payload = {
            "text": "{}",
            "structuredOutput": {
                "title": "Grok title",
                "lead": "Grok lead",
                "body": "Grok body",
                "fact_box": "{}",
                "timestamps": "[]",
            },
        }
        return CommandExecutionResult(
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "grok",
                "provider_fallback": "none",
                "prompt_template": "Title={source_title}\nBody={transcript_text}",
            },
        )
    )

    assert article["title"] == "Grok title"
    assert article["lead"] == "Grok lead"
    assert article["body"] == "Grok body"
    assert article["_llm_provider"] == "grok"
    assert article["_llm_model"] == LLM_GROK_MODEL_DEFAULT
    assert article["_llm_reasoning_effort"] == ""
    assert article["_llm_generated_at"]


def test_restructure_grok_applies_model_and_reasoning_effort() -> None:
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        assert args[0] == _expected_provider_command("grok")
        assert args[args.index("-m") + 1] == "grok-4.5"
        assert args[args.index("--reasoning-effort") + 1] == "high"
        payload = {
            "structuredOutput": {
                "title": "Grok title",
                "lead": "Grok lead",
                "body": "Grok body",
                "fact_box": "{}",
                "timestamps": "[]",
            }
        }
        return CommandExecutionResult(exit_code=0, stdout=json.dumps(payload), stderr="")

    client = UnifiedLlmClient(timeout_seconds=10, runner=fake_runner, command_exists=lambda _: True)
    article = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text="Transcript",
            settings={
                "provider_primary": "grok",
                "provider_fallback": "none",
                "prompt_template": "{transcript_text}",
                "llm_model": {"grok": "grok-4.5"},
                "llm_reasoning_effort": {"grok": "high"},
            },
        )
    )

    assert article["_llm_provider"] == "grok"
    assert article["_llm_model"] == "grok-4.5"
    assert article["_llm_reasoning_effort"] == "high"
