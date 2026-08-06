from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from app.services.llm_errors import (
    LlmClientError,
    looks_like_auth,
    looks_like_refusal,
    trim_error_message,
)
from app.services.llm_schema import ARTICLE_FIELD_KEYS

# Over-escaped markdown usually separates blocks with literal \n/\n\n before headings/lists.
_OVER_ESCAPED_BLOCK_RE = re.compile(
    r"(?:\\n){2,}(?:#{1,6}\s|[-*]\s|\d+\.\s)|(?:\\n)#{1,6}\s",
)
_MIN_LITERAL_NEWLINES_FOR_MARKDOWN = 4


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


def count_literal_newlines(text: str) -> int:
    return str(text or "").count("\\n")


def count_real_newlines(text: str) -> int:
    return str(text or "").count("\n")


def looks_like_over_escaped_markdown(text: str) -> bool:
    """
    True when text looks like markdown that used literal \\n as block separators.

    Requires literal backslash-n to dominate real newlines, plus a markdown-structure
    signal so paths like C:\\notes are not rewritten.
    """
    value = str(text or "")
    literal_n = count_literal_newlines(value)
    real_n = count_real_newlines(value)
    if literal_n < 2 or literal_n <= real_n:
        return False
    if _OVER_ESCAPED_BLOCK_RE.search(value):
        return True
    if (
        literal_n >= _MIN_LITERAL_NEWLINES_FOR_MARKDOWN
        and real_n == 0
        and value.lstrip().startswith("#")
    ):
        return True
    return False


def unescape_over_escaped_text(text: str) -> str:
    return (
        str(text or "")
        .replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .replace("\\t", "\t")
    )


def normalize_article_text(text: str) -> str:
    """Conservatively convert over-escaped markdown newlines into real newlines."""
    value = str(text or "")
    if looks_like_over_escaped_markdown(value):
        value = unescape_over_escaped_text(value)
    return value.strip()


def normalize_fact_box_text(text: str) -> str:
    """
    Normalize markdown fact_box text only.

    Valid JSON fact_box values are left untouched so compact JSON escapes stay valid.
    """
    raw = str(text or "").strip() or "{}"
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        return normalize_article_text(raw) or "{}"
    return raw


def article_body_format_invalid(body: str) -> bool:
    """Detect body formats that cannot be stored as usable markdown."""
    value = str(body or "").strip()
    if not value:
        return True
    if looks_like_over_escaped_markdown(value):
        return True
    # Collapsed single-line dump: multiple ## markers, almost no newlines, no heading lines.
    real_n = count_real_newlines(value)
    if real_n <= 1 and len(value) >= 200 and value.count("##") >= 2:
        heading_lines = sum(1 for line in value.splitlines() if line.lstrip().startswith("#"))
        if heading_lines == 0:
            return True
    return False


def article_fact_box_format_invalid(fact_box: str) -> bool:
    value = str(fact_box or "").strip()
    if not value or value == "{}":
        return False
    try:
        json.loads(value)
    except json.JSONDecodeError:
        return looks_like_over_escaped_markdown(value)
    return False


def coerce_article(payload: Mapping[str, Any], *, provider: str) -> dict[str, str]:
    title = str(payload.get("title") or "").strip()
    lead = normalize_article_text(str(payload.get("lead") or ""))
    body = normalize_article_text(str(payload.get("body") or ""))
    fact_box = normalize_fact_box_text(str(payload.get("fact_box") or "{}"))
    timestamps = str(payload.get("timestamps") or "[]").strip() or "[]"

    if not title or not lead or not body:
        raise LlmClientError(
            "llm_schema_invalid",
            "LLM response has empty required fields",
            provider=provider,
            retryable=True,
        )
    if article_body_format_invalid(body):
        raise LlmClientError(
            "llm_schema_invalid",
            "LLM article body format is invalid",
            provider=provider,
            retryable=True,
        )
    if article_fact_box_format_invalid(fact_box):
        raise LlmClientError(
            "llm_schema_invalid",
            "LLM article fact_box format is invalid",
            provider=provider,
            retryable=True,
        )
    return {
        "title": title,
        "lead": lead,
        "body": body,
        "fact_box": fact_box,
        "timestamps": timestamps,
    }
