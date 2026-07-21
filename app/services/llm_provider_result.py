from __future__ import annotations

from pathlib import Path

from app.services.llm_errors import LlmClientError, classify_command_failure
from app.services.llm_invocation import ProviderCommandResult
from app.services.llm_payload import parse_provider_output
from app.services.llm_response_capture import capture_provider_response


def raise_for_provider_command_failure(
    *,
    provider: str,
    source_title: str,
    result: ProviderCommandResult,
    capture_dir: Path | None,
    capture_max_chars: int,
    include_content: bool,
) -> None:
    if result.exit_code == 0:
        return
    classified = classify_command_failure(
        provider=provider,
        stderr=result.stderr,
        stdout=result.stdout,
        exit_code=result.exit_code,
    )
    capture_provider_response(
        capture_dir=capture_dir,
        capture_max_chars=capture_max_chars,
        include_content=include_content,
        provider=provider,
        source_title=source_title,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        raw_output=result.raw_output,
        parse_error_code=classified.code,
        parse_error_message=str(classified),
        force_include_streams=True,
    )
    raise classified


def parse_and_capture_provider_result(
    *,
    provider: str,
    source_title: str,
    result: ProviderCommandResult,
    capture_dir: Path | None,
    capture_max_chars: int,
    include_content: bool,
) -> dict[str, str]:
    try:
        article = parse_provider_output(provider, result.raw_output)
    except LlmClientError as exc:
        capture_provider_response(
            capture_dir=capture_dir,
            capture_max_chars=capture_max_chars,
            include_content=include_content,
            provider=provider,
            source_title=source_title,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            raw_output=result.raw_output,
            parse_error_code=exc.code,
            parse_error_message=str(exc),
            force_include_streams=True,
        )
        raise

    capture_provider_response(
        capture_dir=capture_dir,
        capture_max_chars=capture_max_chars,
        include_content=include_content,
        provider=provider,
        source_title=source_title,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        raw_output=result.raw_output,
        article=article,
    )
    return article
