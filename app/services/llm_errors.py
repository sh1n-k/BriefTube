from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from app.llm_policy import LLM_PROVIDER_CLAUDE, normalize_llm_provider

REFUSAL_KEYWORDS = (
    "prompt injection",
    "프롬프트 인젝션",
    "cannot comply",
    "unable to comply",
    "refuse",
    "refusal",
    "요청을 거부",
    "답변할 수 없습니다",
    "응답할 수 없습니다",
)
AUTH_KEYWORDS = (
    "not logged in",
    "login required",
    "authentication required",
    "permission denied",
    "unauthorized",
    "forbidden",
    "auth failed",
    # Claude Code: "Not logged in · Please run /login", "Login expired · Please run /login".
    "run /login",
)
# Claude subscription usage limits. Only checked for the claude provider, whose
# usage limit resets on a schedule instead of failing the article.
USAGE_LIMIT_KEYWORDS = (
    "usage limit",
    "limit reached",
    # Claude Code limit notices: "You've hit your weekly limit", "You've reached
    # your ... limit", "You're out of usage credits".
    "you've hit your",
    "you've reached your",
    "you're out of",
)
LLM_PROVIDER_RATE_LIMITED_CODE = "llm_provider_rate_limited"
SCHEMA_KEYWORDS = (
    "invalid_json_schema",
    "response_format",
    "text.format.schema",
)


class LlmClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str | None = None,
        retryable: bool = True,
        stderr_summary: str = "",
        stdout_summary: str = "",
        exit_code: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.retryable = retryable
        self.stderr_summary = str(stderr_summary or "")
        self.stdout_summary = str(stdout_summary or "")
        self.exit_code = exit_code


def looks_like_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in REFUSAL_KEYWORDS)


def looks_like_auth(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in AUTH_KEYWORDS)


def _has_usage_limit_text(text: str) -> bool:
    lowered = text.lower().replace("\u2019", "'")
    return any(keyword in lowered for keyword in USAGE_LIMIT_KEYWORDS)


def claude_envelope_is_usage_limit(envelope: Mapping[str, Any]) -> bool:
    """True when a Claude CLI error envelope reports a usage limit.

    The keywords are only trusted when the model produced no output, so a refusal
    or article text that mentions a limit is not mistaken for one.
    """
    if envelope.get("api_error_status") == 429:
        return True
    usage = envelope.get("usage")
    output_tokens = usage.get("output_tokens") if isinstance(usage, Mapping) else None
    if isinstance(output_tokens, int) and output_tokens > 0:
        return False
    return _has_usage_limit_text(str(envelope.get("error") or envelope.get("result") or ""))


def looks_like_usage_limit(provider: str | None, *, stdout: str, stderr: str) -> bool:
    if provider != LLM_PROVIDER_CLAUDE:
        return False
    try:
        envelope = json.loads(stdout.strip() or "null")
    except json.JSONDecodeError:
        envelope = None
    if isinstance(envelope, Mapping) and claude_envelope_is_usage_limit(envelope):
        return True
    return _has_usage_limit_text(stderr)


def looks_like_schema_mismatch(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in SCHEMA_KEYWORDS) or (
        "response_format" in lowered and "schema" in lowered
    )


def schema_error_code(provider: str) -> str:
    normalized = normalize_llm_provider(provider, allow_none=False)
    return f"llm_provider_schema_invalid_{normalized}"


def trim_error_message(text: str, limit: int = 600) -> str:
    trimmed = str(text or "").strip()
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[:limit]


_SECRET_PATTERNS = (
    (r"(?i)\b(bearer)\s+[A-Za-z0-9._\-+/=]+", r"\1 ***"),
    (r"\b(gho_|ghu_|ghs_|github_pat_)[A-Za-z0-9_]+", r"\1***"),
    (r"\b(sk-[A-Za-z0-9_-]{8,})", r"sk-***"),
    (r"\b(xai-[A-Za-z0-9_-]{8,})", r"xai-***"),
)


def mask_secrets(text: str) -> str:
    masked = str(text or "")
    for pattern, replacement in _SECRET_PATTERNS:
        masked = re.sub(pattern, replacement, masked)
    return masked


def summarize_provider_stream(text: str, *, limit: int = 400) -> str:
    """Single-line, length-limited stream summary for logs (secrets masked)."""
    cleaned = mask_secrets(text)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    return trim_error_message(cleaned, limit=limit)


def _sanitized_command_failure_message(*, exit_code: int, category: str) -> str:
    return f"LLM provider command failed ({category}); exit_code={exit_code}"


def classify_command_failure(
    *,
    provider: str,
    stderr: str,
    stdout: str,
    exit_code: int,
) -> LlmClientError:
    combined = f"{stderr}\n{stdout}".strip()
    stderr_summary = summarize_provider_stream(stderr)
    stdout_summary = summarize_provider_stream(stdout)
    common = {
        "provider": provider,
        "stderr_summary": stderr_summary,
        "stdout_summary": stdout_summary,
        "exit_code": int(exit_code),
    }
    if looks_like_schema_mismatch(combined):
        return LlmClientError(
            schema_error_code(provider),
            _sanitized_command_failure_message(exit_code=exit_code, category="schema"),
            retryable=False,
            **common,
        )
    if looks_like_usage_limit(provider, stdout=stdout, stderr=stderr):
        return LlmClientError(
            LLM_PROVIDER_RATE_LIMITED_CODE,
            _sanitized_command_failure_message(exit_code=exit_code, category="rate_limit"),
            retryable=True,
            **common,
        )
    if looks_like_auth(combined):
        return LlmClientError(
            "llm_provider_auth_required",
            _sanitized_command_failure_message(exit_code=exit_code, category="auth"),
            retryable=False,
            **common,
        )
    if looks_like_refusal(combined):
        return LlmClientError(
            "llm_provider_refused",
            _sanitized_command_failure_message(exit_code=exit_code, category="refusal"),
            retryable=False,
            **common,
        )
    return LlmClientError(
        "llm_provider_command_failed",
        _sanitized_command_failure_message(exit_code=exit_code, category="runtime"),
        retryable=True,
        **common,
    )
