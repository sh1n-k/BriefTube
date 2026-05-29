from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from app.services.llm_errors import LlmClientError


@dataclass(slots=True)
class ProviderAttemptResult:
    provider: str
    article: dict[str, str]


async def run_provider_fallback(
    *,
    providers_to_try: Iterable[str],
    invoke_provider: Callable[[str], Awaitable[dict[str, str]]],
) -> ProviderAttemptResult:
    last_error: LlmClientError | None = None
    for provider in providers_to_try:
        refused_once = False
        while True:
            try:
                return ProviderAttemptResult(
                    provider=provider,
                    article=await invoke_provider(provider),
                )
            except LlmClientError as exc:
                last_error = exc
                if exc.code == "llm_provider_refused" and not refused_once:
                    refused_once = True
                    continue
                break

    if last_error is not None:
        raise last_error

    raise LlmClientError("llm_unknown_error", "LLM provider did not return a result")
