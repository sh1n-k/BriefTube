from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite

import app.repositories._settings as settings_repository
from app.repositories._transcripts import (
    get_settings_map,
)

TRANSCRIPT_GUARD_DEFAULTS: dict[str, str] = {
    "transcript_guard_adaptive_factor": "1.0",
    "transcript_guard_cooldown_until": "",
    "transcript_guard_consecutive_hard_errors": "0",
    "transcript_guard_consecutive_successes": "0",
    "transcript_guard_breaker_state": "closed",
    "transcript_guard_half_open_probe_remaining": "1",
    "transcript_guard_last_channel_id": "",
    "transcript_guard_last_channel_attempt_at": "",
}

_parse_int_setting = settings_repository.parse_int_setting
_parse_float_setting = settings_repository.parse_float_setting


def _parse_datetime_setting(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def get_transcript_guard_state(db: aiosqlite.Connection) -> dict[str, Any]:
    settings = await get_settings_map(db, TRANSCRIPT_GUARD_DEFAULTS)
    adaptive_raw = settings["transcript_guard_adaptive_factor"]
    cooldown_raw = settings["transcript_guard_cooldown_until"]
    hard_raw = settings["transcript_guard_consecutive_hard_errors"]
    success_raw = settings["transcript_guard_consecutive_successes"]
    breaker_state_raw = settings["transcript_guard_breaker_state"]
    probe_raw = settings["transcript_guard_half_open_probe_remaining"]
    last_channel_raw = settings["transcript_guard_last_channel_id"]
    last_channel_attempt_raw = settings["transcript_guard_last_channel_attempt_at"]

    cooldown_until = str(cooldown_raw or "").strip() or None
    breaker_state = str(breaker_state_raw or "closed").strip().lower() or "closed"
    if breaker_state not in {"closed", "open", "half_open"}:
        breaker_state = "closed"
    last_channel_attempt = _parse_datetime_setting(str(last_channel_attempt_raw or ""))
    return {
        "adaptive_factor": _parse_float_setting(
            adaptive_raw, default=1.0, min_value=1.0, max_value=64.0
        ),
        "cooldown_until": cooldown_until,
        "consecutive_hard_errors": _parse_int_setting(
            hard_raw, default=0, min_value=0, max_value=100000
        ),
        "consecutive_successes": _parse_int_setting(
            success_raw, default=0, min_value=0, max_value=100000
        ),
        "breaker_state": breaker_state,
        "half_open_probe_remaining": _parse_int_setting(
            probe_raw, default=1, min_value=0, max_value=1000
        ),
        "last_channel_id": str(last_channel_raw or "").strip() or None,
        "last_channel_attempt_at": last_channel_attempt.isoformat()
        if last_channel_attempt
        else None,
    }


async def save_transcript_guard_state(
    db: aiosqlite.Connection,
    adaptive_factor: float,
    cooldown_until: str | None,
    consecutive_hard_errors: int,
    consecutive_successes: int,
    breaker_state: str = "closed",
    half_open_probe_remaining: int = 1,
    last_channel_id: str | None = None,
    last_channel_attempt_at: str | None = None,
) -> dict[str, Any]:
    safe_breaker_state = str(breaker_state).strip().lower()
    if safe_breaker_state not in {"closed", "open", "half_open"}:
        safe_breaker_state = "closed"
    entries = {
        "transcript_guard_adaptive_factor": str(max(1.0, float(adaptive_factor))),
        "transcript_guard_cooldown_until": str(cooldown_until or ""),
        "transcript_guard_consecutive_hard_errors": str(max(0, int(consecutive_hard_errors))),
        "transcript_guard_consecutive_successes": str(max(0, int(consecutive_successes))),
        "transcript_guard_breaker_state": safe_breaker_state,
        "transcript_guard_half_open_probe_remaining": str(max(0, int(half_open_probe_remaining))),
        "transcript_guard_last_channel_id": str(last_channel_id or "").strip(),
        "transcript_guard_last_channel_attempt_at": str(last_channel_attempt_at or "").strip(),
    }
    for key, value in entries.items():
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (key, value),
        )
    await db.commit()
    return await get_transcript_guard_state(db)


async def reset_transcript_guard_state(db: aiosqlite.Connection) -> dict[str, Any]:
    return await save_transcript_guard_state(
        db,
        adaptive_factor=1.0,
        cooldown_until=None,
        consecutive_hard_errors=0,
        consecutive_successes=0,
        breaker_state="closed",
        half_open_probe_remaining=1,
        last_channel_id=None,
        last_channel_attempt_at=None,
    )
