from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiosqlite

from app.llm_policy import (
    LLM_CODEX_MODEL_DEFAULT,
    LLM_CODEX_MODEL_MAX_LENGTH,
    LLM_CODEX_REASONING_EFFORT_OPTIONS,
    LLM_GEMINI_MODEL_DEFAULT,
    LLM_PROMPT_TEMPLATE_MAX_LENGTH,
    LLM_PROVIDER_CLAUDE,
    LLM_PROVIDER_CODEX,
    LLM_PROVIDER_FALLBACK_OPTIONS,
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OPTIONS,
    LLM_REASONING_EFFORT_GEMINI_OPTIONS,
    LLM_REASONING_EFFORT_OPTIONS,
    normalize_codex_model,
    normalize_llm_provider,
)
from app.repositories._settings import (
    get_settings_map,
    set_setting,
)

LLM_CONFIG_MISSING_ALERT_SENT_KEY = "llm_config_missing_alert_sent"
LLM_SCHEMA_INVALID_ALERT_SENT_KEY = "llm_schema_invalid_alert_sent"
LLM_PROVIDER_PRIMARY_KEY = "llm_provider_primary"
LLM_PROVIDER_FALLBACK_KEY = "llm_provider_fallback"
LLM_PROMPT_TEMPLATE_KEY = "llm_prompt_template"
LLM_MODEL_CODEX_KEY = "llm_model_codex"
LLM_MODEL_CLAUDE_KEY = "llm_model_claude"
LLM_MODEL_GEMINI_KEY = "llm_model_gemini"
LLM_REASONING_EFFORT_CODEX_KEY = "llm_reasoning_effort_codex"
LLM_REASONING_EFFORT_CLAUDE_KEY = "llm_reasoning_effort_claude"
LLM_REASONING_EFFORT_GEMINI_KEY = "llm_reasoning_effort_gemini"
LLM_RUNTIME_LAST_CODE_KEY = "llm_runtime_last_code"
LLM_RUNTIME_LAST_MESSAGE_KEY = "llm_runtime_last_message"
LLM_RUNTIME_LAST_SEEN_AT_KEY = "llm_runtime_last_seen_at"
LLM_PROVIDER_PRIMARY_DEFAULT = LLM_PROVIDER_CODEX
LLM_PROVIDER_FALLBACK_DEFAULT = LLM_PROVIDER_CLAUDE
LLM_MODEL_CLAUDE_MAX_LENGTH = 200
LLM_MODEL_GEMINI_MAX_LENGTH = 200


def _validate_llm_provider_setting(value: str | None, *, allow_none: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    options = LLM_PROVIDER_FALLBACK_OPTIONS if allow_none else LLM_PROVIDER_OPTIONS
    if normalized not in options:
        allowed = ", ".join(sorted(options))
        raise ValueError(f"provider must be one of: {allowed}")
    return normalized


def _validate_llm_prompt_template(value: str | None) -> str:
    prompt = str(value or "")
    if len(prompt) > LLM_PROMPT_TEMPLATE_MAX_LENGTH:
        raise ValueError(f"prompt_template is too long (max {LLM_PROMPT_TEMPLATE_MAX_LENGTH})")
    if prompt.strip() and "{transcript_text}" not in prompt:
        raise ValueError("prompt_template must include {transcript_text}")
    return prompt


def _validate_llm_model_settings(value: Mapping[str, Any]) -> dict[str, str]:
    codex_model = normalize_codex_model(value.get("codex"))
    raw_codex = str(value.get("codex") or "").strip().lower()
    if len(raw_codex) > LLM_CODEX_MODEL_MAX_LENGTH:
        raise ValueError(f"llm_model.codex is too long (max {LLM_CODEX_MODEL_MAX_LENGTH})")
    raw_claude = value.get("claude")
    raw_gemini = value.get("gemini")
    claude_model = str(raw_claude or "").strip()
    gemini_model = str(raw_gemini or "").strip()
    if len(claude_model) > LLM_MODEL_CLAUDE_MAX_LENGTH:
        raise ValueError(f"llm_model.claude is too long (max {LLM_MODEL_CLAUDE_MAX_LENGTH})")
    if len(gemini_model) > LLM_MODEL_GEMINI_MAX_LENGTH:
        raise ValueError(f"llm_model.gemini is too long (max {LLM_MODEL_GEMINI_MAX_LENGTH})")
    if not gemini_model:
        gemini_model = LLM_GEMINI_MODEL_DEFAULT
    return {
        "codex": codex_model,
        "claude": claude_model,
        "gemini": gemini_model,
    }


def _normalize_reasoning_effort(value: Any, *, provider: str) -> str:
    normalized = str(value or "").strip().lower()
    options = (
        LLM_REASONING_EFFORT_GEMINI_OPTIONS
        if provider == LLM_PROVIDER_GEMINI
        else LLM_CODEX_REASONING_EFFORT_OPTIONS
        if provider == LLM_PROVIDER_CODEX
        else LLM_REASONING_EFFORT_OPTIONS
    )
    default_value = "none" if provider == LLM_PROVIDER_GEMINI else ""
    if not normalized:
        return default_value
    if normalized not in options:
        allowed = ", ".join(sorted(options))
        raise ValueError(f"reasoning_effort must be one of: {allowed}")
    return normalized


def _validate_llm_reasoning_effort_settings(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        "codex": _normalize_reasoning_effort(value.get("codex"), provider=LLM_PROVIDER_CODEX),
        "claude": _normalize_reasoning_effort(value.get("claude"), provider=LLM_PROVIDER_CLAUDE),
        "gemini": _normalize_reasoning_effort(value.get("gemini"), provider=LLM_PROVIDER_GEMINI),
    }


async def get_llm_settings(db: aiosqlite.Connection) -> dict[str, Any]:
    settings = await get_settings_map(
        db,
        {
            LLM_PROVIDER_PRIMARY_KEY: LLM_PROVIDER_PRIMARY_DEFAULT,
            LLM_PROVIDER_FALLBACK_KEY: LLM_PROVIDER_FALLBACK_DEFAULT,
            LLM_PROMPT_TEMPLATE_KEY: "",
            LLM_MODEL_CODEX_KEY: LLM_CODEX_MODEL_DEFAULT,
            LLM_MODEL_CLAUDE_KEY: "",
            LLM_MODEL_GEMINI_KEY: LLM_GEMINI_MODEL_DEFAULT,
            LLM_REASONING_EFFORT_CODEX_KEY: "",
            LLM_REASONING_EFFORT_CLAUDE_KEY: "",
            LLM_REASONING_EFFORT_GEMINI_KEY: "none",
        },
    )
    primary_raw = settings[LLM_PROVIDER_PRIMARY_KEY]
    fallback_raw = settings[LLM_PROVIDER_FALLBACK_KEY]
    prompt_raw = settings[LLM_PROMPT_TEMPLATE_KEY]
    model_codex_raw = settings[LLM_MODEL_CODEX_KEY]
    model_claude_raw = settings[LLM_MODEL_CLAUDE_KEY]
    model_gemini_raw = settings[LLM_MODEL_GEMINI_KEY]
    reasoning_effort_codex_raw = settings[LLM_REASONING_EFFORT_CODEX_KEY]
    reasoning_effort_claude_raw = settings[LLM_REASONING_EFFORT_CLAUDE_KEY]
    reasoning_effort_gemini_raw = settings[LLM_REASONING_EFFORT_GEMINI_KEY]

    primary = normalize_llm_provider(primary_raw, allow_none=False)
    fallback = normalize_llm_provider(fallback_raw, allow_none=True)
    if fallback == primary:
        fallback = LLM_PROVIDER_NONE

    prompt_template = str(prompt_raw or "")
    try:
        prompt_template = _validate_llm_prompt_template(prompt_template)
    except ValueError:
        prompt_template = ""
    try:
        model = _validate_llm_model_settings(
            {"codex": model_codex_raw, "claude": model_claude_raw, "gemini": model_gemini_raw}
        )
    except ValueError:
        model = {
            "codex": LLM_CODEX_MODEL_DEFAULT,
            "claude": "",
            "gemini": LLM_GEMINI_MODEL_DEFAULT,
        }
    try:
        reasoning_effort_codex = _normalize_reasoning_effort(
            reasoning_effort_codex_raw,
            provider=LLM_PROVIDER_CODEX,
        )
    except ValueError:
        reasoning_effort_codex = ""
    try:
        reasoning_effort_claude = _normalize_reasoning_effort(
            reasoning_effort_claude_raw,
            provider=LLM_PROVIDER_CLAUDE,
        )
    except ValueError:
        reasoning_effort_claude = ""
    try:
        reasoning_effort_gemini = _normalize_reasoning_effort(
            reasoning_effort_gemini_raw,
            provider=LLM_PROVIDER_GEMINI,
        )
    except ValueError:
        reasoning_effort_gemini = "none"
    reasoning_effort = {
        "codex": reasoning_effort_codex,
        "claude": reasoning_effort_claude,
        "gemini": reasoning_effort_gemini,
    }

    return {
        "provider_primary": primary,
        "provider_fallback": fallback,
        "prompt_template": prompt_template,
        "llm_model": model,
        "llm_reasoning_effort": reasoning_effort,
    }


async def set_llm_settings(
    db: aiosqlite.Connection,
    *,
    provider_primary: str | None = None,
    provider_fallback: str | None = None,
    prompt_template: str | None = None,
    llm_model: Mapping[str, Any] | None = None,
    llm_reasoning_effort: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    current = await get_llm_settings(db)
    next_primary = str(current["provider_primary"])
    next_fallback = str(current["provider_fallback"])
    next_prompt = str(current["prompt_template"])
    current_model = current.get("llm_model", {})
    current_reasoning_effort = current.get("llm_reasoning_effort", {})
    next_model_codex = str(current_model.get("codex", LLM_CODEX_MODEL_DEFAULT))
    next_model_claude = str(current_model.get("claude", ""))
    next_model_gemini = str(current_model.get("gemini", LLM_GEMINI_MODEL_DEFAULT))
    next_reasoning_effort_codex = str(current_reasoning_effort.get("codex", ""))
    next_reasoning_effort_claude = str(current_reasoning_effort.get("claude", ""))
    next_reasoning_effort_gemini = str(current_reasoning_effort.get("gemini", "none"))

    if provider_primary is not None:
        next_primary = _validate_llm_provider_setting(provider_primary, allow_none=False)
    if provider_fallback is not None:
        next_fallback = _validate_llm_provider_setting(provider_fallback, allow_none=True)
    if next_fallback != LLM_PROVIDER_NONE and next_fallback == next_primary:
        raise ValueError("provider_fallback must be different from provider_primary")
    if prompt_template is not None:
        next_prompt = _validate_llm_prompt_template(prompt_template)
    if llm_model is not None:
        next_model_payload = {
            "codex": next_model_codex,
            "claude": next_model_claude,
            "gemini": next_model_gemini,
        }
        next_model_payload.update(
            {key: value for key, value in llm_model.items() if key in {"codex", "claude", "gemini"}}
        )
        validated_model = _validate_llm_model_settings(next_model_payload)
        next_model_codex = validated_model["codex"]
        next_model_claude = validated_model["claude"]
        next_model_gemini = validated_model["gemini"]
    if llm_reasoning_effort is not None:
        next_effort_payload = {
            "codex": next_reasoning_effort_codex,
            "claude": next_reasoning_effort_claude,
            "gemini": next_reasoning_effort_gemini,
        }
        next_effort_payload.update(
            {
                key: value
                for key, value in llm_reasoning_effort.items()
                if key in {"codex", "claude", "gemini"}
            }
        )
        validated_effort = _validate_llm_reasoning_effort_settings(next_effort_payload)
        next_reasoning_effort_codex = validated_effort["codex"]
        next_reasoning_effort_claude = validated_effort["claude"]
        next_reasoning_effort_gemini = validated_effort["gemini"]

    next_settings: dict[str, Any] = {
        "provider_primary": next_primary,
        "provider_fallback": next_fallback,
        "prompt_template": next_prompt,
        "llm_model": {
            "codex": next_model_codex,
            "claude": next_model_claude,
            "gemini": next_model_gemini,
        },
        "llm_reasoning_effort": {
            "codex": next_reasoning_effort_codex,
            "claude": next_reasoning_effort_claude,
            "gemini": next_reasoning_effort_gemini,
        },
    }
    if not persist:
        return next_settings

    await set_setting(db, key=LLM_PROVIDER_PRIMARY_KEY, value=next_primary)
    await set_setting(db, key=LLM_PROVIDER_FALLBACK_KEY, value=next_fallback)
    await set_setting(db, key=LLM_PROMPT_TEMPLATE_KEY, value=next_prompt)
    await set_setting(db, key=LLM_MODEL_CODEX_KEY, value=next_model_codex)
    await set_setting(db, key=LLM_MODEL_CLAUDE_KEY, value=next_model_claude)
    await set_setting(db, key=LLM_MODEL_GEMINI_KEY, value=next_model_gemini)
    await set_setting(db, key=LLM_REASONING_EFFORT_CODEX_KEY, value=next_reasoning_effort_codex)
    await set_setting(db, key=LLM_REASONING_EFFORT_CLAUDE_KEY, value=next_reasoning_effort_claude)
    await set_setting(db, key=LLM_REASONING_EFFORT_GEMINI_KEY, value=next_reasoning_effort_gemini)
    return next_settings
