from __future__ import annotations

import re
from dataclasses import dataclass

EMPTY_TIMESTAMPS = "[]"

_TIME_PATTERN = r"(?:\d{1,2}:)?\d{1,2}:\d{2}"
# "- [MM:SS] 내용", "MM:SS - 내용", "1. [MM:SS] 내용"; the bracket must be closed when opened.
_ENTRY_RE = re.compile(
    r"^\s*(?:(?:[-*\u2022]|\d+[.)])\s*)?"
    rf"(?:\[(?P<bracketed>{_TIME_PATTERN})\]|(?P<bare>{_TIME_PATTERN})(?![\d:]))\s*"
    r"(?:[-\u2013\u2014:|]\s*)?(?P<text>[^\s\]].*?)\s*$"
)
_TRANSCRIPT_MARKER_RE = re.compile(rf"^\[(?P<time>{_TIME_PATTERN})\]", re.MULTILINE)
# Leaked markers sit at a line start (optionally after a list bullet), as in the transcript.
# At most three spaces of indent: four or more is an indented code block.
_LEAKED_MARKER_RE = re.compile(
    rf"^(?P<prefix> {{0,3}}(?:[-*]\s+|\d+[.)]\s+)?)\[{_TIME_PATTERN}\][ \t]*"
)
_CODE_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
# Entries may point slightly past the last marker (markers are spaced ~30s apart).
_MARKER_TAIL_SLACK_SECONDS = 60


@dataclass(frozen=True, slots=True)
class TimestampEntry:
    seconds: int
    label: str
    text: str


def _time_to_seconds(value: str) -> int | None:
    parts = [int(part) for part in value.split(":")]
    if any(part < 0 for part in parts):
        return None
    if len(parts) == 2:
        minutes, seconds = parts
        hours = 0
    else:
        hours, minutes, seconds = parts
        if minutes >= 60:
            return None
    if seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def format_timestamp_label(total_seconds: float) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def parse_timestamp_entries(text: str | None) -> list[TimestampEntry]:
    """Parse '- [MM:SS] 내용' style lines; lines without a valid time are skipped."""
    entries: list[TimestampEntry] = []
    for line in str(text or "").splitlines():
        match = _ENTRY_RE.match(line)
        if match is None:
            continue
        seconds = _time_to_seconds(match.group("bracketed") or match.group("bare"))
        if seconds is None:
            continue
        entries.append(
            TimestampEntry(
                seconds=seconds, label=format_timestamp_label(seconds), text=match.group("text")
            )
        )
    return entries


def normalize_timestamps_text(text: str | None) -> str:
    entries = parse_timestamp_entries(text)
    if not entries:
        return EMPTY_TIMESTAMPS
    return "\n".join(f"- [{entry.label}] {entry.text}" for entry in entries)


def is_grounded_timestamps_text(text: str | None) -> bool:
    """True for the canonical form stored by grounded generation; legacy guesses differ."""
    value = str(text or "").strip()
    return value != EMPTY_TIMESTAMPS and value == normalize_timestamps_text(value)


def transcript_has_position_markers(transcript_text: str | None) -> bool:
    return _TRANSCRIPT_MARKER_RE.search(str(transcript_text or "")) is not None


def ground_timestamps_in_transcript(timestamps: str | None, transcript_text: str | None) -> str:
    """Keep only entries that the transcript's [MM:SS] markers can support."""
    marker_seconds = [
        seconds
        for seconds in (
            _time_to_seconds(match.group("time"))
            for match in _TRANSCRIPT_MARKER_RE.finditer(str(transcript_text or ""))
        )
        if seconds is not None
    ]
    if not marker_seconds:
        return EMPTY_TIMESTAMPS
    limit = max(marker_seconds) + _MARKER_TAIL_SLACK_SECONDS
    entries = [entry for entry in parse_timestamp_entries(timestamps) if entry.seconds <= limit]
    if not entries:
        return EMPTY_TIMESTAMPS
    return "\n".join(f"- [{entry.label}] {entry.text}" for entry in entries)


def strip_position_markers(text: str) -> str:
    """Remove transcript [MM:SS] markers copied to line starts, leaving code blocks intact."""
    lines: list[str] = []
    open_fence = ""
    for line in text.split("\n"):
        fence_match = _CODE_FENCE_RE.match(line)
        if open_fence:
            # A fence closes only with the same character and at least the same length.
            fence = fence_match.group("fence") if fence_match else ""
            if fence and fence[0] == open_fence[0] and len(fence) >= len(open_fence):
                open_fence = ""
        elif fence_match:
            open_fence = fence_match.group("fence")
        else:
            line = _LEAKED_MARKER_RE.sub(r"\g<prefix>", line, count=1)
        lines.append(line)
    return "\n".join(lines)
