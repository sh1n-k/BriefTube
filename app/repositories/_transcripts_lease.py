from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from app.repositories import _settings as settings_repository
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
TRANSCRIPT_WORKER_LEASE_OWNER_KEY = "transcript_worker_lease_owner"
TRANSCRIPT_WORKER_LEASE_UNTIL_KEY = "transcript_worker_lease_until"

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


async def acquire_transcript_worker_lease(
    db: aiosqlite.Connection,
    owner_id: str,
    ttl_seconds: int,
) -> bool:
    safe_owner = str(owner_id).strip()
    if not safe_owner:
        return False
    ttl = max(5, int(ttl_seconds))
    now = datetime.now(UTC)
    await db.execute("BEGIN IMMEDIATE")
    try:
        owner_cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY,),
        )
        owner_row = await owner_cursor.fetchone()
        until_cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (TRANSCRIPT_WORKER_LEASE_UNTIL_KEY,),
        )
        until_row = await until_cursor.fetchone()
        current_owner = str(owner_row["value"] if owner_row is not None else "").strip()
        current_until = _parse_datetime_setting(
            str(until_row["value"] if until_row is not None else "")
        )

        if current_owner and current_owner != safe_owner and current_until and current_until > now:
            await db.rollback()
            return False

        lease_until = now + timedelta(seconds=ttl)
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY, safe_owner),
        )
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_UNTIL_KEY, lease_until.isoformat()),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def renew_transcript_worker_lease(
    db: aiosqlite.Connection,
    owner_id: str,
    ttl_seconds: int,
) -> bool:
    safe_owner = str(owner_id).strip()
    if not safe_owner:
        return False
    await db.execute("BEGIN IMMEDIATE")
    try:
        cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY,),
        )
        row = await cursor.fetchone()
        current_owner = str(row["value"] if row is not None else "").strip()
        if current_owner != safe_owner:
            await db.rollback()
            return False
        ttl = max(5, int(ttl_seconds))
        lease_until = datetime.now(UTC) + timedelta(seconds=ttl)
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_UNTIL_KEY, lease_until.isoformat()),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def release_transcript_worker_lease(db: aiosqlite.Connection, owner_id: str) -> bool:
    safe_owner = str(owner_id).strip()
    if not safe_owner:
        return False
    await db.execute("BEGIN IMMEDIATE")
    try:
        cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY,),
        )
        row = await cursor.fetchone()
        current_owner = str(row["value"] if row is not None else "").strip()
        if current_owner != safe_owner:
            await db.rollback()
            return False
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, '')
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_OWNER_KEY,),
        )
        await db.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES(?, '')
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (TRANSCRIPT_WORKER_LEASE_UNTIL_KEY,),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


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
            probe_raw, default=1, min_value=1, max_value=1000
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
        "transcript_guard_half_open_probe_remaining": str(max(1, int(half_open_probe_remaining))),
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
