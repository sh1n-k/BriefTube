from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.llm_policy import (
    LLM_PROVIDER_CODEX,
    LLM_PROVIDER_GEMINI,
    normalize_llm_provider,
)
from app.services.llm_errors import LlmClientError, schema_error_code

ARTICLE_FIELD_KEYS: tuple[str, ...] = ("title", "lead", "body", "fact_box", "timestamps")
ARTICLE_CORE_KEYS: tuple[str, ...] = ("title", "lead", "body")

ARTICLE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in ARTICLE_FIELD_KEYS},
    "required": list(ARTICLE_FIELD_KEYS),
    "additionalProperties": False,
}
ARTICLE_JSON_SCHEMA_COMPACT = json.dumps(
    ARTICLE_JSON_SCHEMA, ensure_ascii=True, separators=(",", ":")
)


def build_provider_schema(provider: str) -> dict[str, Any]:
    del provider  # schema is currently provider-agnostic; signature kept for future divergence
    required = list(ARTICLE_FIELD_KEYS)
    return {
        "type": "object",
        "properties": {key: {"type": "string"} for key in ARTICLE_FIELD_KEYS},
        "required": required,
        "additionalProperties": False,
    }


def validate_provider_schema(
    provider: str,
    *,
    schema_builder: Callable[[str], dict[str, Any]],
) -> None:
    normalized = normalize_llm_provider(provider, allow_none=False)
    if normalized == LLM_PROVIDER_GEMINI:
        raise LlmClientError(
            schema_error_code(normalized),
            "Gemini CLI does not support strict output schema enforcement",
            provider=normalized,
            retryable=False,
        )
    schema = schema_builder(normalized)
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict):
        raise LlmClientError(
            schema_error_code(normalized),
            "LLM output schema is invalid: properties must be object",
            provider=normalized,
            retryable=False,
        )
    property_keys = {str(key) for key in properties.keys()}
    required_keys = {str(item) for item in required} if isinstance(required, list) else set()
    missing_core = set(ARTICLE_CORE_KEYS) - property_keys
    if missing_core:
        raise LlmClientError(
            schema_error_code(normalized),
            f"LLM output schema is invalid: missing core properties={sorted(missing_core)}",
            provider=normalized,
            retryable=False,
        )
    if normalized == LLM_PROVIDER_CODEX:
        missing_required = property_keys - required_keys
        if missing_required:
            raise LlmClientError(
                schema_error_code(normalized),
                (
                    "LLM output schema is invalid for codex: "
                    f"required must include all properties, missing={sorted(missing_required)}"
                ),
                provider=normalized,
                retryable=False,
            )
    missing_field_keys = set(ARTICLE_FIELD_KEYS) - property_keys
    if missing_field_keys:
        raise LlmClientError(
            schema_error_code(normalized),
            f"LLM output schema is invalid: missing properties={sorted(missing_field_keys)}",
            provider=normalized,
            retryable=False,
        )
