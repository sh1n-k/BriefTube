from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiosqlite

from app.llm_policy import (
    LLM_CODEX_MODEL_DEFAULT,
    LLM_CODEX_MODEL_MAX_LENGTH,
    LLM_CODEX_REASONING_EFFORT_OPTIONS,
    LLM_GEMINI_MODEL_DEFAULT,
    LLM_PROMPT_TEMPLATE_MAX_LENGTH,
    LLM_PROVIDER_CLAUDE,
    LLM_PROVIDER_CODEX,
    LLM_PROVIDER_FALLBACK_OPTIONS,
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OPTIONS,
    LLM_REASONING_EFFORT_GEMINI_OPTIONS,
    LLM_REASONING_EFFORT_OPTIONS,
    normalize_codex_model,
    normalize_llm_provider,
)

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

LLM_CONFIG_MISSING_ALERT_SENT_KEY = "llm_config_missing_alert_sent"
LLM_SCHEMA_INVALID_ALERT_SENT_KEY = "llm_schema_invalid_alert_sent"
LLM_PROVIDER_PRIMARY_KEY = "llm_provider_primary"
LLM_PROVIDER_FALLBACK_KEY = "llm_provider_fallback"
LLM_PROMPT_TEMPLATE_KEY = "llm_prompt_template"
LLM_MODEL_CODEX_KEY = "llm_model_codex"
LLM_MODEL_CLAUDE_KEY = "llm_model_claude"
LLM_MODEL_GEMINI_KEY = "llm_model_gemini"
LLM_REASONING_EFFORT_CODEX_KEY = "llm_reasoning_effort_codex"
LLM_REASONING_EFFORT_CLAUDE_KEY = "llm_reasoning_effort_claude"
LLM_REASONING_EFFORT_GEMINI_KEY = "llm_reasoning_effort_gemini"
LLM_RUNTIME_LAST_CODE_KEY = "llm_runtime_last_code"
LLM_RUNTIME_LAST_MESSAGE_KEY = "llm_runtime_last_message"
LLM_RUNTIME_LAST_SEEN_AT_KEY = "llm_runtime_last_seen_at"
LLM_PROVIDER_PRIMARY_DEFAULT = LLM_PROVIDER_CODEX
LLM_PROVIDER_FALLBACK_DEFAULT = LLM_PROVIDER_CLAUDE
LLM_MODEL_CLAUDE_MAX_LENGTH = 200
LLM_MODEL_GEMINI_MAX_LENGTH = 200

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


def _validate_llm_provider_setting(value: str | None, *, allow_none: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    options = LLM_PROVIDER_FALLBACK_OPTIONS if allow_none else LLM_PROVIDER_OPTIONS
    if normalized not in options:
        allowed = ", ".join(sorted(options))
        raise ValueError(f"provider must be one of: {allowed}")
    return normalized


def _validate_llm_prompt_template(value: str | None) -> str:
    prompt = str(value or "")
    if len(prompt) > LLM_PROMPT_TEMPLATE_MAX_LENGTH:
        raise ValueError(f"prompt_template is too long (max {LLM_PROMPT_TEMPLATE_MAX_LENGTH})")
    if prompt.strip() and "{transcript_text}" not in prompt:
        raise ValueError("prompt_template must include {transcript_text}")
    return prompt


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


def _validate_llm_model_settings(value: Mapping[str, Any]) -> dict[str, str]:
    codex_model = normalize_codex_model(value.get("codex"))
    raw_codex = str(value.get("codex") or "").strip().lower()
    if len(raw_codex) > LLM_CODEX_MODEL_MAX_LENGTH:
        raise ValueError(f"llm_model.codex is too long (max {LLM_CODEX_MODEL_MAX_LENGTH})")
    raw_claude = value.get("claude")
    raw_gemini = value.get("gemini")
    claude_model = str(raw_claude or "").strip()
    gemini_model = str(raw_gemini or "").strip()
    if len(claude_model) > LLM_MODEL_CLAUDE_MAX_LENGTH:
        raise ValueError(f"llm_model.claude is too long (max {LLM_MODEL_CLAUDE_MAX_LENGTH})")
    if len(gemini_model) > LLM_MODEL_GEMINI_MAX_LENGTH:
        raise ValueError(f"llm_model.gemini is too long (max {LLM_MODEL_GEMINI_MAX_LENGTH})")
    if not gemini_model:
        gemini_model = LLM_GEMINI_MODEL_DEFAULT
    return {
        "codex": codex_model,
        "claude": claude_model,
        "gemini": gemini_model,
    }


def _normalize_reasoning_effort(value: Any, *, provider: str) -> str:
    normalized = str(value or "").strip().lower()
    options = (
        LLM_REASONING_EFFORT_GEMINI_OPTIONS
        if provider == LLM_PROVIDER_GEMINI
        else LLM_CODEX_REASONING_EFFORT_OPTIONS
        if provider == LLM_PROVIDER_CODEX
        else LLM_REASONING_EFFORT_OPTIONS
    )
    default_value = "none" if provider == LLM_PROVIDER_GEMINI else ""
    if not normalized:
        return default_value
    if normalized not in options:
        allowed = ", ".join(sorted(options))
        raise ValueError(f"reasoning_effort must be one of: {allowed}")
    return normalized


def _validate_llm_reasoning_effort_settings(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        "codex": _normalize_reasoning_effort(value.get("codex"), provider=LLM_PROVIDER_CODEX),
        "claude": _normalize_reasoning_effort(value.get("claude"), provider=LLM_PROVIDER_CLAUDE),
        "gemini": _normalize_reasoning_effort(value.get("gemini"), provider=LLM_PROVIDER_GEMINI),
    }


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


async def get_llm_settings(db: aiosqlite.Connection) -> dict[str, Any]:
    settings = await get_settings_map(
        db,
        {
            LLM_PROVIDER_PRIMARY_KEY: LLM_PROVIDER_PRIMARY_DEFAULT,
            LLM_PROVIDER_FALLBACK_KEY: LLM_PROVIDER_FALLBACK_DEFAULT,
            LLM_PROMPT_TEMPLATE_KEY: "",
            LLM_MODEL_CODEX_KEY: LLM_CODEX_MODEL_DEFAULT,
            LLM_MODEL_CLAUDE_KEY: "",
            LLM_MODEL_GEMINI_KEY: LLM_GEMINI_MODEL_DEFAULT,
            LLM_REASONING_EFFORT_CODEX_KEY: "",
            LLM_REASONING_EFFORT_CLAUDE_KEY: "",
            LLM_REASONING_EFFORT_GEMINI_KEY: "none",
        },
    )
    primary_raw = settings[LLM_PROVIDER_PRIMARY_KEY]
    fallback_raw = settings[LLM_PROVIDER_FALLBACK_KEY]
    prompt_raw = settings[LLM_PROMPT_TEMPLATE_KEY]
    model_codex_raw = settings[LLM_MODEL_CODEX_KEY]
    model_claude_raw = settings[LLM_MODEL_CLAUDE_KEY]
    model_gemini_raw = settings[LLM_MODEL_GEMINI_KEY]
    reasoning_effort_codex_raw = settings[LLM_REASONING_EFFORT_CODEX_KEY]
    reasoning_effort_claude_raw = settings[LLM_REASONING_EFFORT_CLAUDE_KEY]
    reasoning_effort_gemini_raw = settings[LLM_REASONING_EFFORT_GEMINI_KEY]

    primary = normalize_llm_provider(primary_raw, allow_none=False)
    fallback = normalize_llm_provider(fallback_raw, allow_none=True)
    if fallback == primary:
        fallback = LLM_PROVIDER_NONE

    prompt_template = str(prompt_raw or "")
    try:
        prompt_template = _validate_llm_prompt_template(prompt_template)
    except ValueError:
        prompt_template = ""
    try:
        model = _validate_llm_model_settings(
            {"codex": model_codex_raw, "claude": model_claude_raw, "gemini": model_gemini_raw}
        )
    except ValueError:
        model = {
            "codex": LLM_CODEX_MODEL_DEFAULT,
            "claude": "",
            "gemini": LLM_GEMINI_MODEL_DEFAULT,
        }
    try:
        reasoning_effort_codex = _normalize_reasoning_effort(
            reasoning_effort_codex_raw,
            provider=LLM_PROVIDER_CODEX,
        )
    except ValueError:
        reasoning_effort_codex = ""
    try:
        reasoning_effort_claude = _normalize_reasoning_effort(
            reasoning_effort_claude_raw,
            provider=LLM_PROVIDER_CLAUDE,
        )
    except ValueError:
        reasoning_effort_claude = ""
    try:
        reasoning_effort_gemini = _normalize_reasoning_effort(
            reasoning_effort_gemini_raw,
            provider=LLM_PROVIDER_GEMINI,
        )
    except ValueError:
        reasoning_effort_gemini = "none"
    reasoning_effort = {
        "codex": reasoning_effort_codex,
        "claude": reasoning_effort_claude,
        "gemini": reasoning_effort_gemini,
    }

    return {
        "provider_primary": primary,
        "provider_fallback": fallback,
        "prompt_template": prompt_template,
        "llm_model": model,
        "llm_reasoning_effort": reasoning_effort,
    }


async def set_llm_settings(
    db: aiosqlite.Connection,
    *,
    provider_primary: str | None = None,
    provider_fallback: str | None = None,
    prompt_template: str | None = None,
    llm_model: Mapping[str, Any] | None = None,
    llm_reasoning_effort: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    current = await get_llm_settings(db)
    next_primary = str(current["provider_primary"])
    next_fallback = str(current["provider_fallback"])
    next_prompt = str(current["prompt_template"])
    current_model = current.get("llm_model", {})
    current_reasoning_effort = current.get("llm_reasoning_effort", {})
    next_model_codex = str(current_model.get("codex", LLM_CODEX_MODEL_DEFAULT))
    next_model_claude = str(current_model.get("claude", ""))
    next_model_gemini = str(current_model.get("gemini", LLM_GEMINI_MODEL_DEFAULT))
    next_reasoning_effort_codex = str(current_reasoning_effort.get("codex", ""))
    next_reasoning_effort_claude = str(current_reasoning_effort.get("claude", ""))
    next_reasoning_effort_gemini = str(current_reasoning_effort.get("gemini", "none"))

    if provider_primary is not None:
        next_primary = _validate_llm_provider_setting(provider_primary, allow_none=False)
    if provider_fallback is not None:
        next_fallback = _validate_llm_provider_setting(provider_fallback, allow_none=True)
    if next_fallback != LLM_PROVIDER_NONE and next_fallback == next_primary:
        raise ValueError("provider_fallback must be different from provider_primary")
    if prompt_template is not None:
        next_prompt = _validate_llm_prompt_template(prompt_template)
    if llm_model is not None:
        next_model_payload = {
            "codex": next_model_codex,
            "claude": next_model_claude,
            "gemini": next_model_gemini,
        }
        next_model_payload.update(
            {key: value for key, value in llm_model.items() if key in {"codex", "claude", "gemini"}}
        )
        validated_model = _validate_llm_model_settings(next_model_payload)
        next_model_codex = validated_model["codex"]
        next_model_claude = validated_model["claude"]
        next_model_gemini = validated_model["gemini"]
    if llm_reasoning_effort is not None:
        next_effort_payload = {
            "codex": next_reasoning_effort_codex,
            "claude": next_reasoning_effort_claude,
            "gemini": next_reasoning_effort_gemini,
        }
        next_effort_payload.update(
            {
                key: value
                for key, value in llm_reasoning_effort.items()
                if key in {"codex", "claude", "gemini"}
            }
        )
        validated_effort = _validate_llm_reasoning_effort_settings(next_effort_payload)
        next_reasoning_effort_codex = validated_effort["codex"]
        next_reasoning_effort_claude = validated_effort["claude"]
        next_reasoning_effort_gemini = validated_effort["gemini"]

    next_settings: dict[str, Any] = {
        "provider_primary": next_primary,
        "provider_fallback": next_fallback,
        "prompt_template": next_prompt,
        "llm_model": {
            "codex": next_model_codex,
            "claude": next_model_claude,
            "gemini": next_model_gemini,
        },
        "llm_reasoning_effort": {
            "codex": next_reasoning_effort_codex,
            "claude": next_reasoning_effort_claude,
            "gemini": next_reasoning_effort_gemini,
        },
    }
    if not persist:
        return next_settings

    await set_setting(db, key=LLM_PROVIDER_PRIMARY_KEY, value=next_primary)
    await set_setting(db, key=LLM_PROVIDER_FALLBACK_KEY, value=next_fallback)
    await set_setting(db, key=LLM_PROMPT_TEMPLATE_KEY, value=next_prompt)
    await set_setting(db, key=LLM_MODEL_CODEX_KEY, value=next_model_codex)
    await set_setting(db, key=LLM_MODEL_CLAUDE_KEY, value=next_model_claude)
    await set_setting(db, key=LLM_MODEL_GEMINI_KEY, value=next_model_gemini)
    await set_setting(db, key=LLM_REASONING_EFFORT_CODEX_KEY, value=next_reasoning_effort_codex)
    await set_setting(db, key=LLM_REASONING_EFFORT_CLAUDE_KEY, value=next_reasoning_effort_claude)
    await set_setting(db, key=LLM_REASONING_EFFORT_GEMINI_KEY, value=next_reasoning_effort_gemini)
    return next_settings
