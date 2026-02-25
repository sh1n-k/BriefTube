from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from typing import Any

CHANNEL_ID_RE = re.compile(r"UC[-_A-Za-z0-9]{22}")
YOUTUBE_URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/[^\s\"'<>]+", re.IGNORECASE)
HANDLE_RE = re.compile(r"@[A-Za-z0-9_.-]+")


def _unique(values: list[str]) -> list[str]:
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
    return _unique(tokens)


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
    return _unique(raw_parts)


def parse_takeout_file(filename: str, content: bytes) -> list[str]:
    parsed = parse_takeout_file_details(filename, content)
    flattened: list[str] = []
    for item in parsed.direct_channels:
        flattened.append(item["channel_id"])
        flattened.append(item["channel_name"])
        flattened.append(item["channel_url"])
    flattened.extend(parsed.inputs)
    return _unique(flattened)


def parse_takeout_file_details(filename: str, content: bytes) -> ParsedTakeout:
    lowered = filename.lower()
    if lowered.endswith(".json"):
        return ParsedTakeout(direct_channels=[], inputs=_parse_json(content))
    if lowered.endswith(".csv"):
        return _parse_csv(content)

    text = content.decode("utf-8", errors="ignore")
    tokens = _extract_tokens(text)
    return ParsedTakeout(direct_channels=[], inputs=tokens)


def _parse_csv(content: bytes) -> ParsedTakeout:
    text = content.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))

    entries: list[str] = []
    direct_channels: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    id_aliases = {"channelid", "channelidentifier", "id"}
    title_aliases = {"channeltitle", "channelname", "title", "name"}
    url_aliases = {"channelurl", "url", "link"}

    for row in reader:
        normalized_row = {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}

        channel_id = _find_value(normalized_row, id_aliases)
        channel_name = _find_value(normalized_row, title_aliases)
        channel_url = _find_value(normalized_row, url_aliases)

        if CHANNEL_ID_RE.fullmatch(channel_id) and channel_name:
            if channel_id not in seen_ids:
                seen_ids.add(channel_id)
                direct_channels.append(
                    {
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "channel_url": channel_url or f"https://www.youtube.com/channel/{channel_id}",
                    }
                )
            continue

        for key, value in row.items():
            if value is None:
                continue
            key_lower = (key or "").lower()
            candidate = value.strip()
            if not candidate:
                continue
            if "channel" in key_lower or "url" in key_lower or "id" in key_lower or "title" in key_lower or "name" in key_lower:
                entries.append(candidate)
                entries.extend(_extract_tokens(candidate))

    return ParsedTakeout(
        direct_channels=direct_channels,
        inputs=_unique(entries),
    )


def _parse_json(content: bytes) -> list[str]:
    parsed = json.loads(content.decode("utf-8", errors="ignore"))
    entries: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_lower = key.lower()
                if isinstance(value, str):
                    stripped = value.strip()
                    if not stripped:
                        continue
                    if any(term in key_lower for term in ["channel", "name", "title", "url", "id", "handle"]):
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
    return _unique(entries)
