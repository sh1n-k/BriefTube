from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _capture_text(value: str, max_chars: int) -> tuple[str, bool, int]:
    text = str(value or "")
    chars = len(text)
    if chars <= max_chars:
        return text, False, chars
    return text[:max_chars], True, chars


def capture_provider_response(
    *,
    capture_dir: Path | None,
    capture_max_chars: int,
    include_content: bool,
    provider: str,
    source_title: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    raw_output: str,
    parse_error_code: str | None = None,
    parse_error_message: str | None = None,
    article: Mapping[str, str] | None = None,
    force_include_streams: bool = False,
    stream_max_chars: int = 4_000,
) -> None:
    if capture_dir is None:
        return
    try:
        capture_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(UTC).strftime("%Y%m%d")
        capture_file = capture_dir / f"llm_response_{day}.jsonl"
        max_chars = max(1_000, int(capture_max_chars))
        # Failures may force stderr/stdout text for diagnostics without storing
        # full successful article bodies (include_content still gates those).
        stream_limit = max(256, min(max_chars, int(stream_max_chars)))
        include_streams = bool(include_content or force_include_streams)
        stdout_text, stdout_truncated, stdout_chars = _capture_text(
            stdout, stream_limit if force_include_streams and not include_content else max_chars
        )
        stderr_text, stderr_truncated, stderr_chars = _capture_text(
            stderr, stream_limit if force_include_streams and not include_content else max_chars
        )
        raw_text, raw_truncated, raw_chars = _capture_text(raw_output, max_chars)
        payload: dict[str, Any] = {
            "id": str(uuid4()),
            "captured_at": datetime.now(UTC).isoformat(),
            "provider": provider,
            "source_title": source_title,
            "exit_code": int(exit_code),
            "parse": {
                "ok": parse_error_code is None,
                "error_code": parse_error_code or "",
                "error_message": parse_error_message or "",
            },
            "stdout": {
                "text": stdout_text if include_streams else "",
                "chars": stdout_chars,
                "truncated": stdout_truncated,
            },
            "stderr": {
                "text": stderr_text if include_streams else "",
                "chars": stderr_chars,
                "truncated": stderr_truncated,
            },
            "raw_output": {
                "text": raw_text if include_content else "",
                "chars": raw_chars,
                "truncated": raw_truncated,
            },
        }
        if article is not None:
            payload["article"] = _article_capture_payload(article, include_content=include_content)
        with capture_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")
    except Exception:
        # Capture must never break LLM pipeline flow.
        return


def _article_capture_payload(
    article: Mapping[str, str], *, include_content: bool
) -> dict[str, str | int]:
    if include_content:
        return {
            "title": str(article.get("title") or ""),
            "lead": str(article.get("lead") or ""),
            "body": str(article.get("body") or ""),
            "fact_box": str(article.get("fact_box") or "{}"),
            "timestamps": str(article.get("timestamps") or "[]"),
        }
    return {
        "title_chars": len(str(article.get("title") or "")),
        "lead_chars": len(str(article.get("lead") or "")),
        "body_chars": len(str(article.get("body") or "")),
        "fact_box_chars": len(str(article.get("fact_box") or "{}")),
        "timestamps_chars": len(str(article.get("timestamps") or "[]")),
    }
