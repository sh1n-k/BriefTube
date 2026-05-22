from __future__ import annotations

import pytest

from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS,
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    compact_header_overrides,
    default_transcript_request_headers,
    merge_with_default_headers,
    parse_headers_from_fields,
    parse_headers_multiline,
    validate_complete_header_fields,
)


def test_default_headers_cover_fixed_keys() -> None:
    headers = default_transcript_request_headers()
    assert list(headers.keys()) == list(TRANSCRIPT_REQUEST_HEADER_KEYS)


def test_parse_headers_multiline_supports_fixed_keys() -> None:
    parsed = parse_headers_multiline(
        "User-Agent: Mozilla/5.0 Test\nAccept-Language: ko-KR,ko;q=1.0\nDNT:\n"
    )
    assert parsed["User-Agent"] == "Mozilla/5.0 Test"
    assert parsed["Accept-Language"] == "ko-KR,ko;q=1.0"
    assert parsed["DNT"] == ""


def test_parse_headers_multiline_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unsupported header key"):
        parse_headers_multiline("X-Test: hello")


def test_compact_header_overrides_keeps_only_non_default_values() -> None:
    defaults = default_transcript_request_headers()
    compact = compact_header_overrides(
        {
            "User-Agent": "Mozilla/5.0 Custom",
            "Accept-Language": defaults["Accept-Language"],
            "DNT": "",
        },
        strict=True,
    )
    assert compact == {"User-Agent": "Mozilla/5.0 Custom"}


def test_merge_with_default_headers_applies_partial_override() -> None:
    merged = merge_with_default_headers({"Accept-Language": "ko-KR,ko;q=1.0"})
    assert merged["Accept-Language"] == "ko-KR,ko;q=1.0"
    assert merged["User-Agent"] == default_transcript_request_headers()["User-Agent"]


def test_parse_headers_from_fields_reads_fixed_field_names() -> None:
    parsed, has_any = parse_headers_from_fields(
        {
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["User-Agent"]: "Mozilla/5.0 Form",
            TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["Accept-Language"]: "ko-KR,ko;q=1.0",
        }
    )
    assert has_any is True
    assert parsed == {
        "User-Agent": "Mozilla/5.0 Form",
        "Accept-Language": "ko-KR,ko;q=1.0",
    }


def test_parse_headers_from_fields_reports_absent_fields() -> None:
    parsed, has_any = parse_headers_from_fields({"unrelated": "value"})
    assert has_any is False
    assert parsed == {}


def test_validate_complete_header_fields_accepts_all_keys() -> None:
    payload = {
        TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS[key]: "value"
        for key in TRANSCRIPT_REQUEST_HEADER_KEYS
    }
    validate_complete_header_fields(payload)


def test_validate_complete_header_fields_rejects_missing_keys() -> None:
    payload = {
        TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS["User-Agent"]: "Mozilla/5.0",
    }
    with pytest.raises(ValueError, match="header fields input requires all fixed keys"):
        validate_complete_header_fields(payload)
