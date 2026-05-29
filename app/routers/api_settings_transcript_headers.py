from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.repositories import transcripts as transcripts_repo
from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS,
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    TRANSCRIPT_REQUEST_HEADER_PROFILE,
    compact_header_overrides,
    default_transcript_request_headers,
    format_headers_multiline,
    merge_with_default_headers,
    parse_headers_from_fields,
    parse_headers_multiline,
    validate_complete_header_fields,
)

router = APIRouter(tags=["api"])


def build_transcript_header_payload(overrides: dict[str, str]) -> dict[str, object]:
    compact = compact_header_overrides(overrides, strict=False)
    values = merge_with_default_headers(compact)
    defaults = default_transcript_request_headers()
    return {
        "profile": TRANSCRIPT_REQUEST_HEADER_PROFILE,
        "keys": list(TRANSCRIPT_REQUEST_HEADER_KEYS),
        "field_names": dict(TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS),
        "defaults": defaults,
        "values": values,
        "multiline": format_headers_multiline(values),
    }


@router.put("/settings/transcript-request-headers")
async def set_transcript_request_headers(request: Request):
    content_type = request.headers.get("content-type", "")
    try:
        parsed: dict[str, str] = {}
        has_field_input = False
        raw_text = ""
        if "application/json" in content_type:
            payload = await request.json() or {}
            if isinstance(payload, dict):
                parsed, has_field_input = parse_headers_from_fields(payload)
                raw_text = str(
                    payload.get("headers_text", payload.get("transcript_request_headers", "")) or ""
                )
            if has_field_input and raw_text.strip():
                raise ValueError("mixed header input modes are not allowed")
            if has_field_input:
                validate_complete_header_fields(payload if isinstance(payload, dict) else {})
            elif raw_text.strip():
                parsed = parse_headers_multiline(raw_text)
            else:
                raise ValueError("empty header payload is not allowed")
        else:
            form = await request.form()
            form_payload = {key: form.get(key) for key in form.keys()}
            parsed, has_field_input = parse_headers_from_fields(form_payload)
            raw_text = str(form.get("transcript_request_headers", "") or "")
            if has_field_input and raw_text.strip():
                raise ValueError("mixed header input modes are not allowed")
            if has_field_input:
                validate_complete_header_fields(form_payload)
            elif raw_text.strip():
                parsed = parse_headers_multiline(raw_text)
            else:
                raise ValueError("empty header payload is not allowed")

        overrides = compact_header_overrides(parsed, strict=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved_overrides = await transcripts_repo.save_transcript_request_header_overrides(
        request.app.state.runtime.db,
        overrides,
    )
    applied_values = merge_with_default_headers(saved_overrides)
    request.app.state.runtime.transcript_service.apply_transcript_request_headers(applied_values)
    payload = build_transcript_header_payload(saved_overrides)
    return {
        "ok": True,
        "transcript_request_headers": payload,
    }
