from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

CHANNEL_ID_RE = re.compile(r"UC[-_A-Za-z0-9]{22}")
YOUTUBE_URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/[^\s\"'<>]+", re.IGNORECASE)
HANDLE_RE = re.compile(r"@[A-Za-z0-9_.-]+")


def _decode_bytes(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def unique_preserve_order(values: list[str] | Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _extract_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    tokens.extend(CHANNEL_ID_RE.findall(text))
    tokens.extend(YOUTUBE_URL_RE.findall(text))
    tokens.extend(HANDLE_RE.findall(text))
    return unique_preserve_order(tokens)


def _normalize_header(header: str) -> str:
    return "".join(ch for ch in header.lower() if ch.isalnum())


def _find_value(row: dict[str, str], aliases: set[str]) -> str:
    for key, value in row.items():
        normalized = _normalize_header(key)
        if normalized in aliases:
            return (value or "").strip()
    return ""


@dataclass(slots=True)
class ParsedTakeout:
    direct_channels: list[dict[str, str]]
    inputs: list[str]


def parse_bulk_text_inputs(text: str) -> list[str]:
    raw_parts: list[str] = []
    for line in text.replace(";", "\n").replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        raw_parts.append(line)
    return unique_preserve_order(raw_parts)


def parse_takeout_file(filename: str, content: bytes) -> list[str]:
    parsed = parse_takeout_file_details(filename, content)
    flattened: list[str] = []
    for item in parsed.direct_channels:
        flattened.append(item["channel_id"])
        flattened.append(item["channel_name"])
        flattened.append(item["channel_url"])
    flattened.extend(parsed.inputs)
    return unique_preserve_order(flattened)


def parse_takeout_file_details(filename: str, content: bytes) -> ParsedTakeout:
    lowered = filename.lower()
    if lowered.endswith(".json"):
        return ParsedTakeout(direct_channels=[], inputs=_parse_json(content))
    if lowered.endswith(".csv"):
        return _parse_csv(content)

    text = _decode_bytes(content)
    tokens = _extract_tokens(text)
    return ParsedTakeout(direct_channels=[], inputs=tokens)


def _parse_csv(content: bytes) -> ParsedTakeout:
    text = _decode_bytes(content)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    entries: list[str] = []
    direct_channels: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    id_aliases = {"channelid", "channelidentifier", "id", "채널id", "채널아이디", "아이디"}
    title_aliases = {
        "channeltitle",
        "channelname",
        "title",
        "name",
        "채널명",
        "채널이름",
        "이름",
        "제목",
    }
    url_aliases = {"channelurl", "url", "link", "채널url", "채널링크", "주소", "링크"}
    fallback_header_terms = (
        "channel",
        "url",
        "id",
        "title",
        "name",
        "handle",
        "채널",
        "이름",
        "제목",
        "링크",
        "주소",
        "아이디",
        "핸들",
    )

    for row in reader:
        normalized_row = {
            str(key or "").strip(): str(value or "").strip() for key, value in row.items()
        }

        channel_id = _find_value(normalized_row, id_aliases)
        channel_name = _find_value(normalized_row, title_aliases)
        channel_url = _find_value(normalized_row, url_aliases)

        if CHANNEL_ID_RE.fullmatch(channel_id):
            if channel_id not in seen_ids:
                seen_ids.add(channel_id)
                direct_channels.append(
                    {
                        "channel_id": channel_id,
                        "channel_name": channel_name or channel_id,
                        "channel_url": channel_url
                        or f"https://www.youtube.com/channel/{channel_id}",
                    }
                )
            continue

        for key, value in row.items():
            if value is None:
                continue
            key_normalized = _normalize_header(key or "")
            candidate = value.strip()
            if not candidate:
                continue
            if any(term in key_normalized for term in fallback_header_terms):
                entries.append(candidate)
                entries.extend(_extract_tokens(candidate))

    return ParsedTakeout(
        direct_channels=direct_channels,
        inputs=unique_preserve_order(entries),
    )


def _parse_json(content: bytes) -> list[str]:
    parsed = json.loads(_decode_bytes(content))
    entries: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_lower = key.lower()
                if isinstance(value, str):
                    stripped = value.strip()
                    if not stripped:
                        continue
                    if any(
                        term in key_lower
                        for term in ["channel", "name", "title", "url", "id", "handle"]
                    ):
                        entries.append(stripped)
                    entries.extend(_extract_tokens(stripped))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            stripped = node.strip()
            if stripped:
                entries.extend(_extract_tokens(stripped))

    walk(parsed)
    return unique_preserve_order(entries)
