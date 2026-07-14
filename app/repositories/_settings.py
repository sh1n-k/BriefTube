from __future__ import annotations

from collections.abc import Mapping

import aiosqlite

WORKER_SETTING_KEY_MAP: dict[str, str] = {
    "rss": "worker_rss_enabled",
    "transcript": "worker_transcript_enabled",
    "llm": "worker_llm_enabled",
    "notifier": "worker_notifier_enabled",
}

WORKER_SETTING_DEFAULTS: dict[str, bool] = {
    "rss": True,
    "transcript": True,
    "llm": True,
    "notifier": True,
}

TELEGRAM_BOT_TOKEN_KEY = "telegram_bot_token"  # noqa: S105 -- settings key name
TELEGRAM_CHAT_ID_KEY = "telegram_chat_id"
TELEGRAM_BOT_TOKEN_MAX_LENGTH = 512
TELEGRAM_CHAT_ID_MAX_LENGTH = 128

RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY = "rss_bootstrap_lookback_days"
RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT = 60
RETENTION_DAYS_KEY = "retention_days"
RETENTION_DAYS_DEFAULT = 180
RSS_FEED_MODE_KEY = "rss_feed_mode"
RSS_FEED_MODE_DEFAULT = "long_form_only"
RSS_FEED_MODE_OPTIONS = {"all", "long_form_only"}
VIDEOS_PER_PAGE_KEY = "videos_per_page"
VIDEOS_PER_PAGE_DEFAULT = 8


def parse_bool_setting(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_int_setting(value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def parse_float_setting(
    value: str | None, default: float, min_value: float, max_value: float
) -> float:
    try:
        parsed = float(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


async def get_settings_map(
    db: aiosqlite.Connection,
    values: Mapping[str, str | None],
) -> dict[str, str | None]:
    defaults = {str(key): default for key, default in values.items()}
    keys = list(defaults.keys())
    if not keys:
        return {}

    placeholders = ",".join("?" for _ in keys)
    cursor = await db.execute(
        f"""
        SELECT key, value
        FROM app_settings
        WHERE key IN ({placeholders})
        """,
        tuple(keys),
    )
    rows = await cursor.fetchall()
    resolved = defaults.copy()
    for row in rows:
        resolved[str(row["key"])] = str(row["value"])
    return resolved


async def get_setting(db: aiosqlite.Connection, key: str, default: str | None = None) -> str | None:
    cursor = await db.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (key,),
    )
    row = await cursor.fetchone()
    if row is None:
        return default
    return str(row["value"])


async def set_setting(db: aiosqlite.Connection, key: str, value: str) -> None:
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


def _validate_telegram_setting(
    value: str | None,
    *,
    key_name: str,
    max_length: int,
) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > max_length:
        raise ValueError(f"{key_name} is too long (max {max_length})")
    return normalized


async def get_worker_settings(db: aiosqlite.Connection) -> dict[str, bool]:
    result: dict[str, bool] = {}
    defaults = {
        key: ("true" if WORKER_SETTING_DEFAULTS.get(worker, True) else "false")
        for worker, key in WORKER_SETTING_KEY_MAP.items()
    }
    settings = await get_settings_map(db, defaults)
    for worker, key in WORKER_SETTING_KEY_MAP.items():
        default = WORKER_SETTING_DEFAULTS.get(worker, True)
        raw = settings.get(key)
        result[worker] = parse_bool_setting(raw, default=default)
    return result


async def set_worker_settings(db: aiosqlite.Connection, values: dict[str, bool]) -> dict[str, bool]:
    for worker, enabled in values.items():
        key = WORKER_SETTING_KEY_MAP.get(worker)
        if not key:
            continue
        await set_setting(db, key=key, value="true" if bool(enabled) else "false")
    return await get_worker_settings(db)


async def is_worker_enabled(db: aiosqlite.Connection, worker: str) -> bool:
    key = WORKER_SETTING_KEY_MAP.get(worker)
    if not key:
        return True
    default = WORKER_SETTING_DEFAULTS.get(worker, True)
    raw = await get_setting(db, key=key, default="true" if default else "false")
    return parse_bool_setting(raw, default=default)


async def get_policy_settings(db: aiosqlite.Connection) -> dict[str, int | str]:
    settings = await get_settings_map(
        db,
        {
            RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY: str(RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT),
            RETENTION_DAYS_KEY: str(RETENTION_DAYS_DEFAULT),
            RSS_FEED_MODE_KEY: RSS_FEED_MODE_DEFAULT,
        },
    )
    lookback_raw = settings[RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY]
    retention_raw = settings[RETENTION_DAYS_KEY]
    feed_mode_raw = settings[RSS_FEED_MODE_KEY]
    feed_mode = str(feed_mode_raw).strip().lower() if feed_mode_raw else RSS_FEED_MODE_DEFAULT
    if feed_mode not in RSS_FEED_MODE_OPTIONS:
        feed_mode = RSS_FEED_MODE_DEFAULT
    return {
        "rss_bootstrap_lookback_days": parse_int_setting(
            lookback_raw,
            default=RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT,
            min_value=1,
            max_value=3650,
        ),
        "retention_days": parse_int_setting(
            retention_raw,
            default=RETENTION_DAYS_DEFAULT,
            min_value=1,
            max_value=3650,
        ),
        "rss_feed_mode": feed_mode,
    }


async def set_policy_settings(
    db: aiosqlite.Connection,
    rss_bootstrap_lookback_days: int | None = None,
    retention_days: int | None = None,
    rss_feed_mode: str | None = None,
) -> dict[str, int | str]:
    current = await get_policy_settings(db)
    lookback_value = int(current["rss_bootstrap_lookback_days"])
    retention_value = int(current["retention_days"])

    if rss_bootstrap_lookback_days is not None:
        lookback_value = parse_int_setting(
            str(rss_bootstrap_lookback_days),
            default=lookback_value,
            min_value=1,
            max_value=3650,
        )
        await set_setting(db, key=RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY, value=str(lookback_value))

    if retention_days is not None:
        retention_value = parse_int_setting(
            str(retention_days),
            default=retention_value,
            min_value=1,
            max_value=3650,
        )
        await set_setting(db, key=RETENTION_DAYS_KEY, value=str(retention_value))

    if rss_feed_mode is not None:
        normalized = str(rss_feed_mode).strip().lower()
        if normalized not in RSS_FEED_MODE_OPTIONS:
            normalized = RSS_FEED_MODE_DEFAULT
        await set_setting(db, key=RSS_FEED_MODE_KEY, value=normalized)

    return await get_policy_settings(db)


async def get_videos_per_page_setting(db: aiosqlite.Connection) -> int:
    raw = (
        await get_settings_map(
            db,
            {VIDEOS_PER_PAGE_KEY: str(VIDEOS_PER_PAGE_DEFAULT)},
        )
    )[VIDEOS_PER_PAGE_KEY]
    return parse_int_setting(
        raw,
        default=VIDEOS_PER_PAGE_DEFAULT,
        min_value=1,
        max_value=100,
    )


async def set_videos_per_page_setting(db: aiosqlite.Connection, value: int) -> int:
    normalized = parse_int_setting(
        str(value),
        default=VIDEOS_PER_PAGE_DEFAULT,
        min_value=1,
        max_value=100,
    )
    await set_setting(db, key=VIDEOS_PER_PAGE_KEY, value=str(normalized))
    return normalized


async def get_telegram_settings(db: aiosqlite.Connection) -> dict[str, str]:
    settings = await get_settings_map(
        db,
        {
            TELEGRAM_BOT_TOKEN_KEY: "",
            TELEGRAM_CHAT_ID_KEY: "",
        },
    )
    try:
        bot_token = _validate_telegram_setting(
            settings[TELEGRAM_BOT_TOKEN_KEY],
            key_name="bot_token",
            max_length=TELEGRAM_BOT_TOKEN_MAX_LENGTH,
        )
    except ValueError:
        bot_token = ""
    try:
        chat_id = _validate_telegram_setting(
            settings[TELEGRAM_CHAT_ID_KEY],
            key_name="chat_id",
            max_length=TELEGRAM_CHAT_ID_MAX_LENGTH,
        )
    except ValueError:
        chat_id = ""
    return {
        "bot_token": bot_token,
        "chat_id": chat_id,
    }


async def set_telegram_settings(
    db: aiosqlite.Connection,
    *,
    bot_token: str | None = None,
    chat_id: str | None = None,
    clear_bot_token: bool = False,
    clear_chat_id: bool = False,
) -> dict[str, str]:
    if clear_bot_token and bot_token is not None and str(bot_token).strip():
        raise ValueError("bot_token cannot be provided when clear_bot_token is true")
    if clear_chat_id and chat_id is not None and str(chat_id).strip():
        raise ValueError("chat_id cannot be provided when clear_chat_id is true")

    current = await get_telegram_settings(db)
    next_bot_token = str(current["bot_token"])
    next_chat_id = str(current["chat_id"])

    if clear_bot_token:
        next_bot_token = ""
    elif bot_token is not None:
        candidate = _validate_telegram_setting(
            bot_token,
            key_name="bot_token",
            max_length=TELEGRAM_BOT_TOKEN_MAX_LENGTH,
        )
        if candidate:
            next_bot_token = candidate

    if clear_chat_id:
        next_chat_id = ""
    elif chat_id is not None:
        candidate = _validate_telegram_setting(
            chat_id,
            key_name="chat_id",
            max_length=TELEGRAM_CHAT_ID_MAX_LENGTH,
        )
        if candidate:
            next_chat_id = candidate

    await set_setting(db, key=TELEGRAM_BOT_TOKEN_KEY, value=next_bot_token)
    await set_setting(db, key=TELEGRAM_CHAT_ID_KEY, value=next_chat_id)
    return await get_telegram_settings(db)
