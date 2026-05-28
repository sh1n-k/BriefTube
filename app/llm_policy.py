from __future__ import annotations

from typing import Any

LLM_PROVIDER_CODEX = "codex"
LLM_PROVIDER_CLAUDE = "claude"
LLM_PROVIDER_GEMINI = "gemini"
LLM_PROVIDER_NONE = "none"

LLM_PROVIDER_OPTIONS = {LLM_PROVIDER_CODEX, LLM_PROVIDER_CLAUDE, LLM_PROVIDER_GEMINI}
LLM_PROVIDER_FALLBACK_OPTIONS = {LLM_PROVIDER_NONE, *LLM_PROVIDER_OPTIONS}
LLM_PROMPT_TEMPLATE_MAX_LENGTH = 20_000
LLM_CODEX_MODEL_DEFAULT = "gpt-5.3-codex"
LLM_CODEX_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("gpt-5.3-codex", "GPT-5.3 Codex"),
    ("gpt-5.4", "GPT-5.4"),
)
LLM_CODEX_MODEL_VALUES = {value for value, _label in LLM_CODEX_MODEL_OPTIONS}
LLM_GEMINI_MODEL_DEFAULT = "gemini-3.1-pro-preview"
LLM_CODEX_MODEL_MAX_LENGTH = 200
LLM_CODEX_REASONING_EFFORT_OPTIONS = {"low", "medium", "high", "xhigh"}
LLM_REASONING_EFFORT_OPTIONS = {"low", "medium", "high"}
LLM_REASONING_EFFORT_GEMINI_OPTIONS = {"none", "low", "medium", "high"}


def normalize_llm_provider(value: str | None, *, allow_none: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    options = LLM_PROVIDER_FALLBACK_OPTIONS if allow_none else LLM_PROVIDER_OPTIONS
    if normalized in options:
        return normalized
    return LLM_PROVIDER_NONE if allow_none else LLM_PROVIDER_CODEX


def normalize_codex_model(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized:
        return normalized[:LLM_CODEX_MODEL_MAX_LENGTH]
    return LLM_CODEX_MODEL_DEFAULT
