from __future__ import annotations

import asyncio
import json

from app.services.llm_capabilities import LlmCapabilityProbe


def test_codex_capability_probe_parses_bundled_models_and_efforts() -> None:
    async def fake_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
        assert args == ["codex", "debug", "models"]
        payload = {
            "models": [
                {
                    "slug": "gpt-5.5",
                    "display_name": "GPT-5.5",
                    "visibility": "list",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "low"},
                        {"effort": "medium"},
                        {"effort": "high"},
                        {"effort": "xhigh"},
                    ],
                },
                {
                    "slug": "hidden-model",
                    "display_name": "Hidden",
                    "visibility": "hidden",
                    "supported_reasoning_levels": [{"effort": "low"}],
                },
            ]
        }
        return 0, json.dumps(payload), ""

    probe = LlmCapabilityProbe(
        command_exists=lambda name: name == "codex",
        runner=fake_runner,
    )

    result = asyncio.run(probe.get_codex_capabilities())

    assert result.available is True
    assert result.source == "codex-debug-models"
    assert [(model.value, model.label) for model in result.models] == [("gpt-5.5", "GPT-5.5")]
    assert result.reasoning_efforts == ("low", "medium", "high", "xhigh")


def test_codex_capability_probe_uses_fallback_when_codex_missing() -> None:
    probe = LlmCapabilityProbe(command_exists=lambda _name: False)

    result = asyncio.run(probe.get_codex_capabilities())

    assert result.available is False
    assert result.source == "fallback"
    assert result.error == "codex command not found"
    assert ("gpt-5.3-codex", "GPT-5.3 Codex") in [
        (model.value, model.label) for model in result.models
    ]
    assert result.reasoning_efforts == ("low", "medium", "high", "xhigh")


def test_codex_capability_probe_falls_back_to_bundled_catalog() -> None:
    calls: list[list[str]] = []

    async def fake_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
        calls.append(args)
        if args == ["codex", "debug", "models"]:
            return 1, "", "network unavailable"
        return (
            0,
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-bundled",
                            "display_name": "GPT Bundled",
                            "supported_reasoning_levels": [{"effort": "medium"}],
                        }
                    ]
                }
            ),
            "",
        )

    probe = LlmCapabilityProbe(
        command_exists=lambda name: name == "codex",
        runner=fake_runner,
    )

    result = asyncio.run(probe.get_codex_capabilities())

    assert result.available is True
    assert result.source == "codex-debug-models-bundled"
    assert result.models[0].value == "gpt-bundled"
    assert calls == [
        ["codex", "debug", "models"],
        ["codex", "debug", "models", "--bundled"],
    ]


def test_codex_capability_probe_does_not_expose_command_output_on_failure() -> None:
    async def fake_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
        return 1, "", "secret path /Users/example/.codex/auth.json"

    probe = LlmCapabilityProbe(
        command_exists=lambda name: name == "codex",
        runner=fake_runner,
    )

    result = asyncio.run(probe.get_codex_capabilities())

    assert result.available is False
    assert result.error == "codex-debug-models exited 1"
    assert "secret" not in result.error


def test_codex_capability_probe_caches_until_refresh() -> None:
    calls = 0

    async def fake_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
        nonlocal calls
        calls += 1
        payload = {
            "models": [
                {
                    "slug": f"gpt-test-{calls}",
                    "display_name": f"GPT Test {calls}",
                    "supported_reasoning_levels": [{"effort": "low"}],
                }
            ]
        }
        return 0, json.dumps(payload), ""

    probe = LlmCapabilityProbe(
        command_exists=lambda name: name == "codex",
        runner=fake_runner,
        ttl_seconds=60,
    )

    first = asyncio.run(probe.get_codex_capabilities())
    second = asyncio.run(probe.get_codex_capabilities())
    refreshed = asyncio.run(probe.get_codex_capabilities(refresh=True))

    assert first.models[0].value == "gpt-test-1"
    assert second.models[0].value == "gpt-test-1"
    assert refreshed.models[0].value == "gpt-test-2"
    assert calls == 2
