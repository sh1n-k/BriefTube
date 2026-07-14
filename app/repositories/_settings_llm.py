from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiosqlite

from app.llm_policy import (
    LLM_CODEX_MODEL_DEFAULT,
    LLM_CODEX_MODEL_MAX_LENGTH,
    LLM_CODEX_REASONING_EFFORT_OPTIONS,
    LLM_PROMPT_TEMPLATE_MAX_LENGTH,
    LLM_PROVIDER_CODEX,
    LLM_PROVIDER_NONE,
    normalize_codex_model,
)
from app.repositories._settings import get_settings_map, set_setting

LLM_CONFIG_MISSING_ALERT_SENT_KEY = "llm_config_missing_alert_sent"
LLM_SCHEMA_INVALID_ALERT_SENT_KEY = "llm_schema_invalid_alert_sent"
LLM_PROMPT_TEMPLATE_KEY = "llm_prompt_template"
LLM_MODEL_CODEX_KEY = "llm_model_codex"
LLM_REASONING_EFFORT_CODEX_KEY = "llm_reasoning_effort_codex"
LLM_MAX_CONCURRENT_KEY = "llm_max_concurrent"
LLM_RUNTIME_LAST_CODE_KEY = "llm_runtime_last_code"
LLM_RUNTIME_LAST_MESSAGE_KEY = "llm_runtime_last_message"
LLM_RUNTIME_LAST_SEEN_AT_KEY = "llm_runtime_last_seen_at"
LLM_MAX_CONCURRENT_DEFAULT = 1
LLM_MAX_CONCURRENT_LIMIT = 4


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
    return {"codex": codex_model}


def _normalize_reasoning_effort(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    if normalized not in LLM_CODEX_REASONING_EFFORT_OPTIONS:
        allowed = ", ".join(sorted(LLM_CODEX_REASONING_EFFORT_OPTIONS))
        raise ValueError(f"reasoning_effort must be one of: {allowed}")
    return normalized


def _validate_llm_reasoning_effort_settings(value: Mapping[str, Any]) -> dict[str, str]:
    return {"codex": _normalize_reasoning_effort(value.get("codex"))}


def _validate_llm_max_concurrent(value: Any) -> int:
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"max_concurrent must be between 1 and {LLM_MAX_CONCURRENT_LIMIT}"
        ) from exc
    if normalized < 1 or normalized > LLM_MAX_CONCURRENT_LIMIT:
        raise ValueError(f"max_concurrent must be between 1 and {LLM_MAX_CONCURRENT_LIMIT}")
    return normalized


async def get_llm_settings(db: aiosqlite.Connection) -> dict[str, Any]:
    settings = await get_settings_map(
        db,
        {
            LLM_PROMPT_TEMPLATE_KEY: "",
            LLM_MODEL_CODEX_KEY: LLM_CODEX_MODEL_DEFAULT,
            LLM_REASONING_EFFORT_CODEX_KEY: "",
            LLM_MAX_CONCURRENT_KEY: str(LLM_MAX_CONCURRENT_DEFAULT),
        },
    )
    prompt_raw = settings[LLM_PROMPT_TEMPLATE_KEY]
    model_codex_raw = settings[LLM_MODEL_CODEX_KEY]
    reasoning_effort_codex_raw = settings[LLM_REASONING_EFFORT_CODEX_KEY]
    max_concurrent_raw = settings[LLM_MAX_CONCURRENT_KEY]

    prompt_template = str(prompt_raw or "")
    try:
        prompt_template = _validate_llm_prompt_template(prompt_template)
    except ValueError:
        prompt_template = ""
    try:
        model = _validate_llm_model_settings({"codex": model_codex_raw})
    except ValueError:
        model = {"codex": LLM_CODEX_MODEL_DEFAULT}
    try:
        reasoning_effort_codex = _normalize_reasoning_effort(reasoning_effort_codex_raw)
    except ValueError:
        reasoning_effort_codex = ""
    try:
        max_concurrent = _validate_llm_max_concurrent(max_concurrent_raw)
    except ValueError:
        max_concurrent = LLM_MAX_CONCURRENT_DEFAULT

    return {
        "provider_primary": LLM_PROVIDER_CODEX,
        "provider_fallback": LLM_PROVIDER_NONE,
        "prompt_template": prompt_template,
        "llm_model": model,
        "llm_reasoning_effort": {"codex": reasoning_effort_codex},
        "max_concurrent": max_concurrent,
    }


async def set_llm_settings(
    db: aiosqlite.Connection,
    *,
    prompt_template: str | None = None,
    llm_model: Mapping[str, Any] | None = None,
    llm_reasoning_effort: Mapping[str, Any] | None = None,
    max_concurrent: Any | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    current = await get_llm_settings(db)
    next_prompt = str(current["prompt_template"])
    current_model = current.get("llm_model", {})
    current_reasoning_effort = current.get("llm_reasoning_effort", {})
    next_model_codex = str(current_model.get("codex", LLM_CODEX_MODEL_DEFAULT))
    next_reasoning_effort_codex = str(current_reasoning_effort.get("codex", ""))
    next_max_concurrent = int(current.get("max_concurrent", LLM_MAX_CONCURRENT_DEFAULT))

    if prompt_template is not None:
        next_prompt = _validate_llm_prompt_template(prompt_template)
    if llm_model is not None:
        validated_model = _validate_llm_model_settings(
            {"codex": llm_model.get("codex", next_model_codex)}
        )
        next_model_codex = validated_model["codex"]
    if llm_reasoning_effort is not None:
        validated_effort = _validate_llm_reasoning_effort_settings(
            {"codex": llm_reasoning_effort.get("codex", next_reasoning_effort_codex)}
        )
        next_reasoning_effort_codex = validated_effort["codex"]
    if max_concurrent is not None:
        next_max_concurrent = _validate_llm_max_concurrent(max_concurrent)

    next_settings: dict[str, Any] = {
        "provider_primary": LLM_PROVIDER_CODEX,
        "provider_fallback": LLM_PROVIDER_NONE,
        "prompt_template": next_prompt,
        "llm_model": {"codex": next_model_codex},
        "llm_reasoning_effort": {"codex": next_reasoning_effort_codex},
        "max_concurrent": next_max_concurrent,
    }
    if not persist:
        return next_settings

    await set_setting(db, key=LLM_PROMPT_TEMPLATE_KEY, value=next_prompt)
    await set_setting(db, key=LLM_MODEL_CODEX_KEY, value=next_model_codex)
    await set_setting(db, key=LLM_REASONING_EFFORT_CODEX_KEY, value=next_reasoning_effort_codex)
    await set_setting(db, key=LLM_MAX_CONCURRENT_KEY, value=str(next_max_concurrent))
    return next_settings
