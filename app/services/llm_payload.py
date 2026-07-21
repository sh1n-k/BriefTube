from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.services.llm_errors import (
    LlmClientError,
    looks_like_auth,
    looks_like_refusal,
    trim_error_message,
)
from app.services.llm_schema import ARTICLE_FIELD_KEYS


def parse_provider_output(provider: str, stdout: str) -> dict[str, str]:
    parsed = load_json(stdout, provider)
    payload = extract_article_payload(parsed, provider)
    return coerce_article(payload, provider=provider)


def load_json(raw: str, provider: str) -> Any:
    stripped = raw.strip()
    if not stripped:
        raise LlmClientError(
            "llm_schema_invalid",
            "LLM output is empty",
            provider=provider,
            retryable=True,
        )
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    if looks_like_auth(stripped):
        raise LlmClientError(
            "llm_provider_auth_required",
            trim_error_message(stripped),
            provider=provider,
            retryable=False,
        )
    if looks_like_refusal(stripped):
        raise LlmClientError(
            "llm_provider_refused",
            trim_error_message(stripped),
            provider=provider,
            retryable=False,
        )

    raise LlmClientError(
        "llm_schema_invalid",
        "LLM output is not valid JSON",
        provider=provider,
        retryable=True,
    )


def extract_article_payload(data: Any, provider: str) -> Mapping[str, Any]:
    if isinstance(data, dict):
        if bool(data.get("is_error")):
            subtype = str(data.get("subtype") or "").strip().lower()
            message = str(data.get("error") or data.get("result") or "provider error").strip()
            message = trim_error_message(message)
            if "refus" in subtype or looks_like_refusal(message):
                raise LlmClientError(
                    "llm_provider_refused",
                    message,
                    provider=provider,
                    retryable=False,
                )
            if looks_like_auth(message):
                raise LlmClientError(
                    "llm_provider_auth_required",
                    message,
                    provider=provider,
                    retryable=False,
                )
            raise LlmClientError(
                "llm_provider_command_failed",
                message,
                provider=provider,
                retryable=True,
            )

        if is_article_payload(data):
            return data

        structured = data.get("structured_output")
        if isinstance(structured, dict) and is_article_payload(structured):
            return structured

        structured_camel = data.get("structuredOutput")
        if isinstance(structured_camel, dict) and is_article_payload(structured_camel):
            return structured_camel

        nested_result = data.get("result")
        if isinstance(nested_result, dict) and is_article_payload(nested_result):
            return nested_result
        if isinstance(nested_result, str):
            if looks_like_auth(nested_result):
                raise LlmClientError(
                    "llm_provider_auth_required",
                    trim_error_message(nested_result),
                    provider=provider,
                    retryable=False,
                )
            if looks_like_refusal(nested_result):
                raise LlmClientError(
                    "llm_provider_refused",
                    trim_error_message(nested_result),
                    provider=provider,
                    retryable=False,
                )

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and is_article_payload(item):
                return item

    raise LlmClientError(
        "llm_schema_invalid",
        "LLM response does not match required article schema",
        provider=provider,
        retryable=True,
    )


def is_article_payload(payload: Mapping[str, Any]) -> bool:
    required = set(ARTICLE_FIELD_KEYS)
    return required.issubset(set(payload.keys()))


def coerce_article(payload: Mapping[str, Any], *, provider: str) -> dict[str, str]:
    title = str(payload.get("title") or "").strip()
    lead = str(payload.get("lead") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title or not lead or not body:
        raise LlmClientError(
            "llm_schema_invalid",
            "LLM response has empty required fields",
            provider=provider,
            retryable=True,
        )
    return {
        "title": title,
        "lead": lead,
        "body": body,
        "fact_box": str(payload.get("fact_box") or "{}"),
        "timestamps": str(payload.get("timestamps") or "[]"),
    }
