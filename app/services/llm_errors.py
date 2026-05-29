from __future__ import annotations

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


class LlmClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str | None = None,
        retryable: bool = True,
    ):
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.retryable = retryable


def looks_like_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in REFUSAL_KEYWORDS)


def looks_like_auth(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in AUTH_KEYWORDS)


def looks_like_schema_mismatch(text: str) -> bool:
    lowered = text.lower()
    return (
        "invalid_json_schema" in lowered
        or ("response_format" in lowered and "schema" in lowered)
        or "text.format.schema" in lowered
    )


def schema_error_code(provider: str) -> str:
    normalized = normalize_llm_provider(provider, allow_none=False)
    return f"llm_provider_schema_invalid_{normalized}"


def trim_error_message(text: str, limit: int = 600) -> str:
    trimmed = str(text or "").strip()
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[:limit]


def classify_command_failure(
    *,
    provider: str,
    stderr: str,
    stdout: str,
    exit_code: int,
) -> LlmClientError:
    combined = f"{stderr}\n{stdout}".strip()
    message = trim_error_message(combined or f"provider exit code={exit_code}")
    if looks_like_schema_mismatch(message):
        return LlmClientError(
            schema_error_code(provider),
            message,
            provider=provider,
            retryable=False,
        )
    if looks_like_auth(message):
        return LlmClientError(
            "llm_provider_auth_required",
            message,
            provider=provider,
            retryable=False,
        )
    if looks_like_refusal(message):
        return LlmClientError(
            "llm_provider_refused",
            message,
            provider=provider,
            retryable=False,
        )
    return LlmClientError(
        "llm_provider_command_failed",
        message,
        provider=provider,
        retryable=True,
    )
