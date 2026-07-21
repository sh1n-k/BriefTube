from __future__ import annotations

import re

from app.llm_policy import normalize_llm_provider

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
)
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
