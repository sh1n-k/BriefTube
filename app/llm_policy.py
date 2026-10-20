from __future__ import annotations

from typing import Any

LLM_PROVIDER_CODEX = "codex"
LLM_PROVIDER_GROK = "grok"
LLM_PROVIDER_CURSOR = "cursor"
LLM_PROVIDER_CLAUDE = "claude"
LLM_PROVIDER_NONE = "none"
LLM_PROVIDER_VALUES = {
    LLM_PROVIDER_CODEX,
    LLM_PROVIDER_GROK,
    LLM_PROVIDER_CURSOR,
    LLM_PROVIDER_CLAUDE,
}

LLM_PROMPT_TEMPLATE_MAX_LENGTH = 20_000
LLM_CODEX_MODEL_DEFAULT = "gpt-5.3-codex"
LLM_CODEX_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("gpt-5.3-codex", "GPT-5.3 Codex"),
    ("gpt-5.4", "GPT-5.4"),
)
LLM_CODEX_MODEL_MAX_LENGTH = 200
LLM_CODEX_REASONING_EFFORT_OPTIONS = {"low", "medium", "high", "xhigh"}

LLM_GROK_MODEL_DEFAULT = "grok-4.5"
LLM_GROK_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("grok-4.5", "Grok 4.5"),
    ("grok-4.6", "Grok 4.6"),
)
LLM_GROK_MODEL_MAX_LENGTH = 200
# Grok 4.5 Build menu exposes low/medium/high only (no xhigh).
LLM_GROK_REASONING_EFFORT_OPTIONS = {"low", "medium", "high"}
LLM_GROK_REASONING_EFFORT_ORDER: tuple[str, ...] = ("low", "medium", "high")
# Cursor encodes reasoning effort in the model slug, so it has no separate effort setting.
LLM_CURSOR_MODEL_DEFAULT = "cursor-grok-4.6-high"
LLM_CURSOR_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("cursor-grok-4.6-high", "Grok 4.6 High"),
    ("grok-4.7-high", "Grok 4.7 High"),
)
LLM_CURSOR_MODEL_MAX_LENGTH = 200

# Full model IDs, not aliases: the CLI can resolve an alias to an older model.
# Fable is left out because it bills usage credits instead of the subscription.
LLM_CLAUDE_MODEL_DEFAULT = "claude-sonnet-5-5"
LLM_CLAUDE_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("claude-sonnet-5-5", "Sonnet 5.5"),
    ("claude-opus-5-5", "Opus 5.5"),
    ("claude-haiku-5-5", "Haiku 5.5"),
)
LLM_CLAUDE_MODEL_MAX_LENGTH = 200
LLM_CLAUDE_REASONING_EFFORT_OPTIONS = {"low", "medium", "high", "xhigh", "max"}
LLM_CLAUDE_REASONING_EFFORT_ORDER: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
# Claude runs on the user's subscription, which shares usage limits with
# interactive Claude Code. Space out call starts so a backlog does not burst.
LLM_CLAUDE_MIN_CALL_INTERVAL_SECONDS = 60.0


def normalize_llm_provider(value: str | None, *, allow_none: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if allow_none and normalized == LLM_PROVIDER_NONE:
        return LLM_PROVIDER_NONE
    if normalized in LLM_PROVIDER_VALUES:
        return normalized
    return LLM_PROVIDER_NONE if allow_none else LLM_PROVIDER_CODEX


def normalize_codex_model(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized:
        return normalized[:LLM_CODEX_MODEL_MAX_LENGTH]
    return LLM_CODEX_MODEL_DEFAULT


def normalize_grok_model(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized:
        return normalized[:LLM_GROK_MODEL_MAX_LENGTH]
    return LLM_GROK_MODEL_DEFAULT


def normalize_cursor_model(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized:
        return normalized[:LLM_CURSOR_MODEL_MAX_LENGTH]
    return LLM_CURSOR_MODEL_DEFAULT


def normalize_claude_model(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized:
        return normalized[:LLM_CLAUDE_MODEL_MAX_LENGTH]
    return LLM_CLAUDE_MODEL_DEFAULT


def llm_min_call_interval_seconds(provider: str) -> float:
    if provider == LLM_PROVIDER_CLAUDE:
        return LLM_CLAUDE_MIN_CALL_INTERVAL_SECONDS
    return 0.0
