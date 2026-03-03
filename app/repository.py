from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Mapping

import aiosqlite
from app.services.downloads import validate_download_output_dir
from app.services.llm import (
    LLM_CODEX_MODEL_FIXED,
    LLM_PROMPT_TEMPLATE_MAX_LENGTH,
    LLM_PROVIDER_CLAUDE,
    LLM_PROVIDER_CODEX,
    LLM_PROVIDER_FALLBACK_OPTIONS,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OPTIONS,
    LLM_REASONING_EFFORT_OPTIONS,
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
CHANNEL_MANAGEMENT_STATUS_ACTIVE = "active"
CHANNEL_MANAGEMENT_STATUS_INACTIVE = "inactive"
CHANNEL_MANAGEMENT_STATUS_OPTIONS = {
    CHANNEL_MANAGEMENT_STATUS_ACTIVE,
    CHANNEL_MANAGEMENT_STATUS_INACTIVE,
}

ALERT_TYPE_RSS_CHANNEL_NOT_FOUND = "rss_channel_not_found"
ALERT_TYPE_LLM_CONFIG_MISSING = "llm_config_missing"
ALERT_TYPE_LLM_SCHEMA_INVALID = "llm_schema_invalid"
LLM_CONFIG_MISSING_ALERT_SENT_KEY = "llm_config_missing_alert_sent"
LLM_SCHEMA_INVALID_ALERT_SENT_KEY = "llm_schema_invalid_alert_sent"
LLM_PROVIDER_PRIMARY_KEY = "llm_provider_primary"
LLM_PROVIDER_FALLBACK_KEY = "llm_provider_fallback"
LLM_PROMPT_TEMPLATE_KEY = "llm_prompt_template"
LLM_MODEL_CLAUDE_KEY = "llm_model_claude"
LLM_REASONING_EFFORT_CODEX_KEY = "llm_reasoning_effort_codex"
LLM_REASONING_EFFORT_CLAUDE_KEY = "llm_reasoning_effort_claude"
LLM_RUNTIME_LAST_CODE_KEY = "llm_runtime_last_code"
LLM_RUNTIME_LAST_MESSAGE_KEY = "llm_runtime_last_message"
LLM_RUNTIME_LAST_SEEN_AT_KEY = "llm_runtime_last_seen_at"
LLM_PROVIDER_PRIMARY_DEFAULT = LLM_PROVIDER_CODEX
LLM_PROVIDER_FALLBACK_DEFAULT = LLM_PROVIDER_CLAUDE
LLM_MODEL_CLAUDE_MAX_LENGTH = 200
LLM_ARTICLE_PROVIDER_UNKNOWN = "unknown"

RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY = "rss_bootstrap_lookback_days"
RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT = 60
RETENTION_DAYS_KEY = "retention_days"
RETENTION_DAYS_DEFAULT = 180
RSS_FEED_MODE_KEY = "rss_feed_mode"
RSS_FEED_MODE_DEFAULT = "long_form_only"
RSS_FEED_MODE_OPTIONS = {"all", "long_form_only"}
VIDEOS_PER_PAGE_KEY = "videos_per_page"
VIDEOS_PER_PAGE_DEFAULT = 8
DOWNLOAD_DEFAULT_QUALITY_KEY = "download_default_quality"
DOWNLOAD_DEFAULT_OVERWRITE_KEY = "download_default_overwrite"
DOWNLOAD_DEFAULT_OUTPUT_DIR_KEY = "download_output_dir"
DOWNLOAD_QUALITY_DEFAULT = "1080"
DOWNLOAD_OUTPUT_DIR_DEFAULT = "./downloads"
DOWNLOAD_QUALITY_OPTIONS = {"2160", "1440", "1080", "720", "480"}
DOWNLOAD_STATUS_PENDING = "pending"
DOWNLOAD_STATUS_RUNNING = "running"
DOWNLOAD_STATUS_SUCCEEDED = "succeeded"
DOWNLOAD_STATUS_FAILED = "failed"
DOWNLOAD_STATUS_OPTIONS = {
    DOWNLOAD_STATUS_PENDING,
    DOWNLOAD_STATUS_RUNNING,
    DOWNLOAD_STATUS_SUCCEEDED,
    DOWNLOAD_STATUS_FAILED,
}
MANUAL_ARTICLE_JOB_STATUS_PENDING = "pending"
MANUAL_ARTICLE_JOB_STATUS_RUNNING = "running"
MANUAL_ARTICLE_JOB_STATUS_SUCCEEDED = "succeeded"
MANUAL_ARTICLE_JOB_STATUS_FAILED = "failed"
MANUAL_ARTICLE_JOB_STATUS_SKIPPED = "skipped"
MANUAL_ARTICLE_JOB_STATUS_OPTIONS = {
    MANUAL_ARTICLE_JOB_STATUS_PENDING,
    MANUAL_ARTICLE_JOB_STATUS_RUNNING,
    MANUAL_ARTICLE_JOB_STATUS_SUCCEEDED,
    MANUAL_ARTICLE_JOB_STATUS_FAILED,
    MANUAL_ARTICLE_JOB_STATUS_SKIPPED,
}
MANUAL_ARTICLE_ENQUEUE_RETRY_PIPELINE_STATUSES = {
    "auto_paused",
    "transcript_done",
    "transcript_failed",
    "no_subtitle",
    "llm_failed",
    "manual_review",
}
MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES = {
    "transcript_pending",
    "transcript_processing",
    "llm_pending",
    "llm_processing",
    "done",
}
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
TRANSCRIPT_ERROR_MESSAGE_MAX_LENGTH = 512
TRANSCRIPT_WORKER_LEASE_OWNER_KEY = "transcript_worker_lease_owner"
TRANSCRIPT_WORKER_LEASE_UNTIL_KEY = "transcript_worker_lease_until"
TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY = "transcript_request_headers_overrides_json"
PIPELINE_STATUS_KEYS: tuple[str, ...] = (
    "auto_paused",
    "transcript_pending",
    "transcript_processing",
    "transcript_done",
    "transcript_failed",
    "no_subtitle",
    "llm_pending",
    "llm_processing",
    "llm_failed",
    "manual_review",
    "done",
)
VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES: tuple[str, ...] = (
    "auto_paused",
    "transcript_done",
    "transcript_failed",
    "no_subtitle",
    "llm_pending",
    "manual_review",
    "done",
)
TRANSCRIPT_QUEUE_STATUSES = ("transcript_pending", "transcript_processing", "transcript_failed", "no_subtitle")
LLM_QUEUE_STATUSES = ("llm_pending", "llm_processing", "llm_failed", "manual_review")
logger = logging.getLogger(__name__)

DEFAULT_CATEGORY_NAME = "미분류"
CATEGORY_PROCESSING_STAGE_OFF = "off"
CATEGORY_PROCESSING_STAGE_TRANSCRIPT_ONLY = "transcript_only"
CATEGORY_PROCESSING_STAGE_FULL = "full"
CATEGORY_PROCESSING_STAGE_OPTIONS = {
    CATEGORY_PROCESSING_STAGE_OFF,
    CATEGORY_PROCESSING_STAGE_TRANSCRIPT_ONLY,
    CATEGORY_PROCESSING_STAGE_FULL,
}
CHANNEL_METADATA_STATUS_NEVER = "never"
CHANNEL_METADATA_STATUS_PENDING = "pending"
CHANNEL_METADATA_STATUS_RUNNING = "running"
CHANNEL_METADATA_STATUS_SUCCESS = "success"
CHANNEL_METADATA_STATUS_FAILED = "failed"
CHANNEL_METADATA_STATUS_RATE_LIMITED = "rate_limited"
CHANNEL_METADATA_STATUS_OPTIONS = {
    CHANNEL_METADATA_STATUS_NEVER,
    CHANNEL_METADATA_STATUS_PENDING,
    CHANNEL_METADATA_STATUS_RUNNING,
    CHANNEL_METADATA_STATUS_SUCCESS,
    CHANNEL_METADATA_STATUS_FAILED,
    CHANNEL_METADATA_STATUS_RATE_LIMITED,
}
CHANNEL_METADATA_REFRESH_INTERVAL_DAYS_DEFAULT = 30
CHANNEL_METADATA_RATE_LIMIT_BACKOFF_MINUTES = (360, 720, 1440)
CHANNEL_METADATA_FAILURE_BACKOFF_MINUTES = (15, 30, 60, 180)
CHANNEL_METADATA_MAX_RETRY_COUNT = 12


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _rows_to_dicts(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{k: row[k] for k in row.keys()} for row in rows]


def _thumbnail_url(path: str | None, video_id: str | None = None) -> str | None:
    if not path:
        safe_video_id = str(video_id or "").strip()
        if not safe_video_id:
            return None
        return f"https://i.ytimg.com/vi/{safe_video_id}/hqdefault.jpg"
    filename = Path(path).name
    if not filename:
        safe_video_id = str(video_id or "").strip()
        if not safe_video_id:
            return None
        return f"https://i.ytimg.com/vi/{safe_video_id}/hqdefault.jpg"
    return f"/thumbnails/{filename}"


def _parse_bool_setting(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int_setting(value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _parse_float_setting(value: str | None, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def normalize_category_processing_stage(value: str | None, *, default: str = CATEGORY_PROCESSING_STAGE_OFF) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CATEGORY_PROCESSING_STAGE_OPTIONS:
        return normalized
    if default in CATEGORY_PROCESSING_STAGE_OPTIONS:
        return default
    return CATEGORY_PROCESSING_STAGE_OFF


def parse_category_processing_stage(value: object | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CATEGORY_PROCESSING_STAGE_OPTIONS:
        return normalized
    allowed = ", ".join(sorted(CATEGORY_PROCESSING_STAGE_OPTIONS))
    raise ValueError(f"processing_stage must be one of: {allowed}")


def next_category_processing_stage(current: str | None) -> str:
    normalized = normalize_category_processing_stage(current)
    if normalized == CATEGORY_PROCESSING_STAGE_OFF:
        return CATEGORY_PROCESSING_STAGE_TRANSCRIPT_ONLY
    if normalized == CATEGORY_PROCESSING_STAGE_TRANSCRIPT_ONLY:
        return CATEGORY_PROCESSING_STAGE_FULL
    return CATEGORY_PROCESSING_STAGE_OFF


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


def _validate_llm_model_settings(value: Mapping[str, Any]) -> dict[str, str]:
    raw_claude = value.get("claude")
    claude_model = str(raw_claude or "").strip()
    if len(claude_model) > LLM_MODEL_CLAUDE_MAX_LENGTH:
        raise ValueError(f"llm_model.claude is too long (max {LLM_MODEL_CLAUDE_MAX_LENGTH})")
    return {
        "codex": LLM_CODEX_MODEL_FIXED,
        "claude": claude_model,
    }


def _normalize_reasoning_effort(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    if normalized not in LLM_REASONING_EFFORT_OPTIONS:
        allowed = ", ".join(sorted(LLM_REASONING_EFFORT_OPTIONS))
        raise ValueError(f"reasoning_effort must be one of: {allowed}")
    return normalized


def _validate_llm_reasoning_effort_settings(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        "codex": _normalize_reasoning_effort(value.get("codex")),
        "claude": _normalize_reasoning_effort(value.get("claude")),
    }


def _with_thumbnail_url(item: dict[str, Any]) -> dict[str, Any]:
    item["thumbnail_url"] = _thumbnail_url(
        item.get("thumbnail_path"),
        item.get("video_id"),
    )
    return item


def _normalize_download_quality(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in DOWNLOAD_QUALITY_OPTIONS:
        return normalized
    return DOWNLOAD_QUALITY_DEFAULT


def normalize_download_quality(value: str | None) -> str:
    return _normalize_download_quality(value)


def normalize_download_status_filter(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in DOWNLOAD_STATUS_OPTIONS:
        return normalized
    return "all"


def normalize_pipeline_status_filter(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES:
        return normalized
    return None


def _parse_datetime_setting(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_error_message(value: str | None) -> str:
    if not value:
        return ""
    trimmed = str(value).strip()
    if len(trimmed) <= TRANSCRIPT_ERROR_MESSAGE_MAX_LENGTH:
        return trimmed
    return trimmed[:TRANSCRIPT_ERROR_MESSAGE_MAX_LENGTH]


def _normalize_article_provider(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in LLM_PROVIDER_OPTIONS:
        return normalized
    return LLM_ARTICLE_PROVIDER_UNKNOWN


def _normalize_article_text_meta(value: str | None, *, max_length: int = 200) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return normalized[:max_length]


def normalize_channel_metadata_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CHANNEL_METADATA_STATUS_OPTIONS:
        return normalized
    return CHANNEL_METADATA_STATUS_NEVER


def _normalize_optional_text(value: object | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if len(raw) > max_length:
        return raw[:max_length]
    return raw


def _normalize_optional_int(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _next_metadata_backoff_minutes(
    *,
    retry_count: int,
    is_rate_limited: bool,
) -> int:
    schedule = (
        CHANNEL_METADATA_RATE_LIMIT_BACKOFF_MINUTES
        if is_rate_limited
        else CHANNEL_METADATA_FAILURE_BACKOFF_MINUTES
    )
    idx = max(0, min(len(schedule) - 1, retry_count))
    return int(schedule[idx])


async def get_default_category_id(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT id FROM categories WHERE is_default = 1 LIMIT 1")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("default category not found")
    return int(row["id"])


async def list_categories(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT c.id, c.name, c.sort_order, c.processing_stage, c.is_default, c.created_at,
               COUNT(ch.channel_id) AS channel_count
        FROM categories c
        LEFT JOIN channels ch ON ch.category_id = c.id
        GROUP BY c.id
        ORDER BY c.sort_order ASC, c.id ASC
        """
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def create_category(db: aiosqlite.Connection, name: str) -> dict[str, Any]:
    name = str(name).strip()
    if not name:
        raise ValueError("category name must not be empty")
    cursor = await db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM categories"
    )
    row = await cursor.fetchone()
    next_order = int(row["next_order"]) if row else 0
    try:
        await db.execute(
            "INSERT INTO categories (name, sort_order, processing_stage) VALUES (?, ?, ?)",
            (name, next_order, CATEGORY_PROCESSING_STAGE_OFF),
        )
    except (aiosqlite.IntegrityError, sqlite3.IntegrityError):
        raise ValueError(f"category name already exists: {name}")
    await db.commit()
    cat_cursor = await db.execute(
        "SELECT id, name, sort_order, processing_stage, is_default, created_at FROM categories WHERE name = ?",
        (name,),
    )
    cat_row = await cat_cursor.fetchone()
    return _row_to_dict(cat_row) or {}


async def rename_category(db: aiosqlite.Connection, category_id: int, name: str) -> int:
    name = str(name).strip()
    if not name:
        raise ValueError("category name must not be empty")
    try:
        cursor = await db.execute(
            "UPDATE categories SET name = ? WHERE id = ?",
            (name, category_id),
        )
    except (aiosqlite.IntegrityError, sqlite3.IntegrityError):
        raise ValueError(f"category name already exists: {name}")
    await db.commit()
    return int(cursor.rowcount or 0)


async def delete_category(db: aiosqlite.Connection, category_id: int) -> dict[str, int]:
    cursor = await db.execute(
        "SELECT is_default FROM categories WHERE id = ?",
        (category_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("category not found")
    if int(row["is_default"]):
        raise ValueError("cannot delete default category")
    default_id = await get_default_category_id(db)
    move_cursor = await db.execute(
        "UPDATE channels SET category_id = ? WHERE category_id = ?",
        (default_id, category_id),
    )
    channels_moved = int(move_cursor.rowcount or 0)
    await db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    await db.commit()
    return {"deleted": 1, "channels_moved": channels_moved}


async def update_category_processing_stage(db: aiosqlite.Connection, category_id: int, processing_stage: str) -> int:
    safe_stage = normalize_category_processing_stage(processing_stage)
    cursor = await db.execute(
        "UPDATE categories SET processing_stage = ? WHERE id = ?",
        (safe_stage, category_id),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def cycle_category_processing_stage(db: aiosqlite.Connection, category_id: int) -> str | None:
    cursor = await db.execute(
        "SELECT processing_stage FROM categories WHERE id = ?",
        (category_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    current_stage = normalize_category_processing_stage(str(row["processing_stage"] or ""))
    next_stage = next_category_processing_stage(current_stage)
    await db.execute(
        "UPDATE categories SET processing_stage = ? WHERE id = ?",
        (next_stage, category_id),
    )
    await db.commit()
    return next_stage


async def reorder_categories(db: aiosqlite.Connection, ordered_ids: list[int]) -> int:
    cursor = await db.execute("SELECT id FROM categories")
    rows = await cursor.fetchall()
    existing_ids = {int(r["id"]) for r in rows}
    input_ids = set(ordered_ids)
    if input_ids != existing_ids:
        missing_in_input = existing_ids - input_ids
        for mid in missing_in_input:
            ordered_ids.append(mid)
    updated = 0
    for idx, cat_id in enumerate(ordered_ids):
        cur = await db.execute(
            "UPDATE categories SET sort_order = ? WHERE id = ?",
            (idx, cat_id),
        )
        updated += int(cur.rowcount or 0)
    await db.commit()
    return updated


async def move_channels_to_category(
    db: aiosqlite.Connection,
    channel_ids: list[str],
    target_category_id: int,
) -> int:
    normalized = [cid for cid in dict.fromkeys(channel_ids) if cid]
    if not normalized:
        return 0
    cat_cursor = await db.execute("SELECT id FROM categories WHERE id = ?", (target_category_id,))
    if await cat_cursor.fetchone() is None:
        raise ValueError("target category not found")
    placeholders = ",".join(["?"] * len(normalized))
    cursor = await db.execute(
        f"UPDATE channels SET category_id = ? WHERE channel_id IN ({placeholders})",
        (target_category_id, *normalized),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def list_channels(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            channel_handle,
            channel_url_canonical,
            channel_thumbnail_url,
            channel_description,
            channel_language_hint,
            metadata_fetched_at,
            metadata_fetch_status,
            metadata_fetch_error,
            metadata_retry_count,
            metadata_next_fetch_at,
            metadata_last_http_status,
            category_id,
            created_at
        FROM channels
        ORDER BY created_at DESC
        """
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


def normalize_channel_management_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CHANNEL_MANAGEMENT_STATUS_OPTIONS:
        return normalized
    return CHANNEL_MANAGEMENT_STATUS_ACTIVE


async def list_channels_for_management(
    db: aiosqlite.Connection,
    status: str = CHANNEL_MANAGEMENT_STATUS_ACTIVE,
    category_id: int | None = None,
) -> list[dict[str, Any]]:
    normalized_status = normalize_channel_management_status(status)
    is_active = 1 if normalized_status == CHANNEL_MANAGEMENT_STATUS_ACTIVE else 0
    params: list[object] = [ALERT_TYPE_RSS_CHANNEL_NOT_FOUND, is_active]
    category_filter = ""
    if category_id is not None:
        category_filter = "AND c.category_id = ?"
        params.append(category_id)
    cursor = await db.execute(
        f"""
        SELECT
            c.channel_id,
            c.channel_name,
            c.rss_url,
            c.is_active,
            c.last_seen_published_at,
            c.category_id,
            c.channel_handle,
            c.channel_url_canonical,
            c.channel_thumbnail_url,
            c.channel_description,
            c.channel_language_hint,
            c.metadata_fetched_at,
            c.metadata_fetch_status,
            c.metadata_fetch_error,
            c.metadata_retry_count,
            c.metadata_next_fetch_at,
            c.metadata_last_http_status,
            c.created_at,
            cat.name AS category_name,
            COALESCE(cat.is_default, 0) AS category_is_default,
            sa.message AS inactive_reason,
            sa.created_at AS inactive_at
        FROM channels c
        LEFT JOIN categories cat ON cat.id = c.category_id
        LEFT JOIN system_alerts sa
          ON sa.id = (
              SELECT s2.id
              FROM system_alerts s2
              WHERE s2.channel_id = c.channel_id
                AND s2.alert_type = ?
              ORDER BY s2.created_at DESC, s2.id DESC
              LIMIT 1
          )
        WHERE c.is_active = ?
        {category_filter}
        ORDER BY c.created_at DESC
        """,
        tuple(params),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def get_channel_name_map(
    db: aiosqlite.Connection,
    channel_ids: list[str],
) -> dict[str, str]:
    normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not normalized:
        return {}
    placeholders = ",".join(["?"] * len(normalized))
    cursor = await db.execute(
        f"""
        SELECT channel_id, channel_name
        FROM channels
        WHERE channel_id IN ({placeholders})
        """,
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    return {str(row["channel_id"]): str(row["channel_name"] or row["channel_id"]) for row in rows}


async def count_channels_by_status(db: aiosqlite.Connection) -> dict[str, int]:
    cursor = await db.execute(
        """
        SELECT
            SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END) AS inactive_count
        FROM channels
        """
    )
    row = await cursor.fetchone()
    active_count = int((row["active_count"] if row else 0) or 0)
    inactive_count = int((row["inactive_count"] if row else 0) or 0)
    return {
        CHANNEL_MANAGEMENT_STATUS_ACTIVE: active_count,
        CHANNEL_MANAGEMENT_STATUS_INACTIVE: inactive_count,
    }


async def add_channel(
    db: aiosqlite.Connection,
    channel_id: str,
    channel_name: str,
    category_id: int | None = None,
    *,
    channel_handle: str | None = None,
    channel_url_canonical: str | None = None,
    channel_thumbnail_url: str | None = None,
    channel_description: str | None = None,
    channel_language_hint: str | None = None,
    metadata_fetch_status: str | None = None,
    metadata_fetch_error: str | None = None,
    metadata_retry_count: int | None = None,
    metadata_next_fetch_at: str | None = None,
    metadata_last_http_status: int | None = None,
    metadata_fetched_at: str | None = None,
) -> dict[str, Any]:
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    if category_id is None:
        category_id = await get_default_category_id(db)
    safe_handle = _normalize_optional_text(channel_handle, max_length=128)
    safe_canonical_url = _normalize_optional_text(channel_url_canonical, max_length=512)
    safe_thumbnail_url = _normalize_optional_text(channel_thumbnail_url, max_length=512)
    safe_description = _normalize_optional_text(channel_description, max_length=4000)
    safe_language_hint = _normalize_optional_text(channel_language_hint, max_length=32)
    safe_fetch_status = (
        normalize_channel_metadata_status(metadata_fetch_status)
        if metadata_fetch_status is not None
        else None
    )
    safe_fetch_error = _normalize_optional_text(metadata_fetch_error, max_length=512)
    safe_retry_count = _normalize_optional_int(metadata_retry_count)
    safe_next_fetch_at = _normalize_optional_text(metadata_next_fetch_at, max_length=64)
    safe_last_http_status = _normalize_optional_int(metadata_last_http_status)
    safe_fetched_at = _normalize_optional_text(metadata_fetched_at, max_length=64)
    await db.execute(
        """
        INSERT INTO channels (
            channel_id,
            channel_name,
            rss_url,
            is_active,
            category_id,
            created_at
        )
        VALUES (?, ?, ?, 1, ?, datetime('now'))
        ON CONFLICT(channel_id) DO UPDATE SET
            channel_name=excluded.channel_name,
            rss_url=excluded.rss_url,
            is_active=1,
            created_at=COALESCE(channels.created_at, datetime('now'))
        """,
        (
            channel_id,
            channel_name,
            rss_url,
            category_id,
        ),
    )
    await db.execute(
        """
        UPDATE channels
        SET created_at = datetime('now')
        WHERE channel_id = ?
          AND (created_at IS NULL OR trim(created_at) = '')
        """,
        (channel_id,),
    )
    if any(
        value is not None
        for value in (
            safe_handle,
            safe_canonical_url,
            safe_thumbnail_url,
            safe_description,
            safe_language_hint,
            safe_fetch_status,
            safe_fetch_error,
            safe_retry_count,
            safe_next_fetch_at,
            safe_last_http_status,
            safe_fetched_at,
        )
    ):
        await db.execute(
            """
            UPDATE channels
            SET
                channel_handle=COALESCE(?, channel_handle),
                channel_url_canonical=COALESCE(?, channel_url_canonical),
                channel_thumbnail_url=COALESCE(?, channel_thumbnail_url),
                channel_description=COALESCE(?, channel_description),
                channel_language_hint=COALESCE(?, channel_language_hint),
                metadata_fetched_at=COALESCE(?, metadata_fetched_at),
                metadata_fetch_status=COALESCE(?, metadata_fetch_status),
                metadata_fetch_error=COALESCE(?, metadata_fetch_error),
                metadata_retry_count=COALESCE(?, metadata_retry_count),
                metadata_next_fetch_at=COALESCE(?, metadata_next_fetch_at),
                metadata_last_http_status=COALESCE(?, metadata_last_http_status)
            WHERE channel_id = ?
            """,
            (
                safe_handle,
                safe_canonical_url,
                safe_thumbnail_url,
                safe_description,
                safe_language_hint,
                safe_fetched_at,
                safe_fetch_status,
                safe_fetch_error,
                safe_retry_count,
                safe_next_fetch_at,
                safe_last_http_status,
                channel_id,
            ),
        )
    await db.commit()

    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            category_id,
            channel_handle,
            channel_url_canonical,
            channel_thumbnail_url,
            channel_description,
            channel_language_hint,
            metadata_fetched_at,
            metadata_fetch_status,
            metadata_fetch_error,
            metadata_retry_count,
            metadata_next_fetch_at,
            metadata_last_http_status,
            created_at
        FROM channels
        WHERE channel_id = ?
        """,
        (channel_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row) or {}


async def get_channel_by_id(db: aiosqlite.Connection, channel_id: str) -> dict[str, Any] | None:
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id:
        return None
    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            category_id,
            channel_handle,
            channel_url_canonical,
            channel_thumbnail_url,
            channel_description,
            channel_language_hint,
            metadata_fetched_at,
            metadata_fetch_status,
            metadata_fetch_error,
            metadata_retry_count,
            metadata_next_fetch_at,
            metadata_last_http_status,
            created_at
        FROM channels
        WHERE channel_id = ?
        LIMIT 1
        """,
        (normalized_channel_id,),
    )
    return _row_to_dict(await cursor.fetchone())


async def enqueue_channel_metadata_refresh(
    db: aiosqlite.Connection,
    *,
    channel_id: str,
) -> int:
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id:
        return 0
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL,
            metadata_next_fetch_at = datetime('now')
        WHERE channel_id = ?
        """,
        (CHANNEL_METADATA_STATUS_PENDING, normalized_channel_id),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def schedule_channel_metadata_backfill(
    db: aiosqlite.Connection,
    *,
    stale_days: int = CHANNEL_METADATA_REFRESH_INTERVAL_DAYS_DEFAULT,
) -> int:
    safe_stale_days = max(1, int(stale_days))
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL,
            metadata_next_fetch_at = datetime('now')
        WHERE metadata_fetch_status != ?
          AND (
            metadata_fetched_at IS NULL
            OR metadata_next_fetch_at IS NULL
            OR metadata_next_fetch_at <= datetime('now')
            OR metadata_fetch_status IN (?, ?, ?)
            OR (julianday('now') - julianday(metadata_fetched_at)) >= ?
          )
        """,
        (
            CHANNEL_METADATA_STATUS_PENDING,
            CHANNEL_METADATA_STATUS_RUNNING,
            CHANNEL_METADATA_STATUS_NEVER,
            CHANNEL_METADATA_STATUS_FAILED,
            CHANNEL_METADATA_STATUS_RATE_LIMITED,
            safe_stale_days,
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def claim_next_channel_metadata_target(db: aiosqlite.Connection) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT channel_id
        FROM channels
        WHERE metadata_fetch_status = ?
          AND (
            metadata_next_fetch_at IS NULL
            OR metadata_next_fetch_at <= datetime('now')
          )
        ORDER BY COALESCE(metadata_next_fetch_at, datetime('now')) ASC, created_at ASC
        LIMIT 1
        """,
        (CHANNEL_METADATA_STATUS_PENDING,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    channel_id = str(row["channel_id"])
    updated = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL
        WHERE channel_id = ?
          AND metadata_fetch_status = ?
        """,
        (
            CHANNEL_METADATA_STATUS_RUNNING,
            channel_id,
            CHANNEL_METADATA_STATUS_PENDING,
        ),
    )
    if int(updated.rowcount or 0) == 0:
        return None
    await db.commit()
    detail_cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            metadata_retry_count,
            metadata_next_fetch_at
        FROM channels
        WHERE channel_id = ?
        LIMIT 1
        """,
        (channel_id,),
    )
    return _row_to_dict(await detail_cursor.fetchone())


async def mark_channel_metadata_succeeded(
    db: aiosqlite.Connection,
    *,
    channel_id: str,
    channel_name: str | None,
    channel_handle: str | None,
    channel_url_canonical: str | None,
    channel_thumbnail_url: str | None,
    channel_description: str | None,
    channel_language_hint: str | None,
    http_status: int | None = None,
    refresh_interval_days: int = CHANNEL_METADATA_REFRESH_INTERVAL_DAYS_DEFAULT,
) -> int:
    safe_channel_name = _normalize_optional_text(channel_name, max_length=255)
    safe_handle = _normalize_optional_text(channel_handle, max_length=128)
    safe_canonical_url = _normalize_optional_text(channel_url_canonical, max_length=512)
    safe_thumbnail_url = _normalize_optional_text(channel_thumbnail_url, max_length=512)
    safe_description = _normalize_optional_text(channel_description, max_length=4000)
    safe_language_hint = _normalize_optional_text(channel_language_hint, max_length=32)
    safe_http_status = _normalize_optional_int(http_status)
    safe_interval_days = max(1, int(refresh_interval_days))
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            channel_name = COALESCE(?, channel_name),
            channel_handle = ?,
            channel_url_canonical = ?,
            channel_thumbnail_url = ?,
            channel_description = ?,
            channel_language_hint = ?,
            metadata_fetched_at = datetime('now'),
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL,
            metadata_retry_count = 0,
            metadata_next_fetch_at = datetime('now', '+' || ? || ' days'),
            metadata_last_http_status = ?
        WHERE channel_id = ?
        """,
        (
            safe_channel_name,
            safe_handle,
            safe_canonical_url,
            safe_thumbnail_url,
            safe_description,
            safe_language_hint,
            CHANNEL_METADATA_STATUS_SUCCESS,
            safe_interval_days,
            safe_http_status,
            channel_id,
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_channel_metadata_failed(
    db: aiosqlite.Connection,
    *,
    channel_id: str,
    error_message: str,
    http_status: int | None = None,
    is_rate_limited: bool = False,
) -> int:
    row_cursor = await db.execute(
        """
        SELECT metadata_retry_count
        FROM channels
        WHERE channel_id = ?
        LIMIT 1
        """,
        (channel_id,),
    )
    row = await row_cursor.fetchone()
    if row is None:
        return 0
    previous_retry_count = int(row["metadata_retry_count"] or 0)
    retry_count = min(CHANNEL_METADATA_MAX_RETRY_COUNT, max(0, previous_retry_count + 1))
    backoff_minutes = _next_metadata_backoff_minutes(
        retry_count=retry_count - 1,
        is_rate_limited=is_rate_limited,
    )
    safe_http_status = _normalize_optional_int(http_status)
    status = (
        CHANNEL_METADATA_STATUS_RATE_LIMITED
        if is_rate_limited
        else CHANNEL_METADATA_STATUS_FAILED
    )
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = ?,
            metadata_retry_count = ?,
            metadata_next_fetch_at = datetime('now', '+' || ? || ' minutes'),
            metadata_last_http_status = ?
        WHERE channel_id = ?
        """,
        (
            status,
            _normalize_optional_text(error_message, max_length=512),
            retry_count,
            backoff_minutes,
            safe_http_status,
            channel_id,
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def enqueue_failed_channel_metadata(
    db: aiosqlite.Connection,
    *,
    status: str | None = None,
    category_id: int | None = None,
) -> int:
    where_clauses = ["metadata_fetch_status IN (?, ?)"]
    params: list[Any] = [
        CHANNEL_METADATA_STATUS_PENDING,
        CHANNEL_METADATA_STATUS_FAILED,
        CHANNEL_METADATA_STATUS_RATE_LIMITED,
    ]
    normalized_status = str(status or "").strip().lower()
    if normalized_status in CHANNEL_MANAGEMENT_STATUS_OPTIONS:
        where_clauses.append("is_active = ?")
        params.append(1 if normalized_status == CHANNEL_MANAGEMENT_STATUS_ACTIVE else 0)
    if category_id is not None:
        where_clauses.append("category_id = ?")
        params.append(int(category_id))
    where_sql = " AND ".join(where_clauses)
    cursor = await db.execute(
        f"""
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = NULL,
            metadata_next_fetch_at = datetime('now')
        WHERE {where_sql}
        """,
        tuple(params),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def recover_stuck_channel_metadata_running(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE channels
        SET
            metadata_fetch_status = ?,
            metadata_fetch_error = COALESCE(metadata_fetch_error, 'metadata worker interrupted'),
            metadata_next_fetch_at = datetime('now', '+15 minutes')
        WHERE metadata_fetch_status = ?
        """,
        (
            CHANNEL_METADATA_STATUS_FAILED,
            CHANNEL_METADATA_STATUS_RUNNING,
        ),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def deactivate_channel(db: aiosqlite.Connection, channel_id: str) -> int:
    cursor = await db.execute(
        "UPDATE channels SET is_active = 0 WHERE channel_id = ?",
        (channel_id,),
    )
    await db.commit()
    return cursor.rowcount


async def reactivate_channel(db: aiosqlite.Connection, channel_id: str) -> int:
    cursor = await db.execute(
        "UPDATE channels SET is_active = 1 WHERE channel_id = ?",
        (channel_id,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def reactivate_channels(db: aiosqlite.Connection, channel_ids: list[str]) -> int:
    normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not normalized:
        return 0
    placeholders = ",".join(["?"] * len(normalized))
    cursor = await db.execute(
        f"UPDATE channels SET is_active = 1 WHERE channel_id IN ({placeholders})",
        tuple(normalized),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def list_active_channels(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT
            channel_id,
            channel_name,
            rss_url,
            is_active,
            last_seen_published_at,
            metadata_fetched_at,
            metadata_fetch_status,
            metadata_next_fetch_at,
            created_at
        FROM channels
        WHERE is_active = 1
        ORDER BY created_at ASC
        """
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def update_channel_watermark(db: aiosqlite.Connection, channel_id: str, published_at: str) -> None:
    await db.execute(
        "UPDATE channels SET last_seen_published_at = ? WHERE channel_id = ?",
        (published_at, channel_id),
    )
    await db.commit()


async def insert_video_if_absent(
    db: aiosqlite.Connection,
    video_id: str,
    channel_id: str,
    title: str,
    upload_time: str,
) -> bool:
    cursor = await db.execute(
        """
        INSERT INTO videos (
            video_id,
            channel_id,
            title,
            upload_time,
            pipeline_status,
            processing_stage_snapshot
        )
        SELECT
            ?,
            ?,
            ?,
            ?,
            CASE
                WHEN lower(trim(coalesce(cat.processing_stage, ''))) = 'off'
                THEN 'auto_paused'
                ELSE 'transcript_pending'
            END,
            CASE lower(trim(coalesce(cat.processing_stage, '')))
                WHEN 'off' THEN 'off'
                WHEN 'transcript_only' THEN 'transcript_only'
                WHEN 'full' THEN 'full'
                ELSE 'full'
            END
        FROM channels ch
        LEFT JOIN categories cat ON cat.id = ch.category_id
        WHERE ch.channel_id = ?
        ON CONFLICT(video_id) DO NOTHING
        """,
        (video_id, channel_id, title, upload_time, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def list_videos(
    db: aiosqlite.Connection,
    channel_id: str | None,
    sort: str,
    order: str,
    page: int,
    limit: int,
    category_id: int | None = None,
    pipeline_status: str | None = None,
) -> list[dict[str, Any]]:
    sort_column = "upload_time" if sort not in {"upload_time", "created_at"} else sort
    order_sql = "ASC" if order.lower() == "asc" else "DESC"
    offset = (max(page, 1) - 1) * max(limit, 1)

    conditions: list[str] = []
    params: list[object] = []
    if channel_id:
        conditions.append("v.channel_id = ?")
        params.append(channel_id)
    if category_id is not None:
        conditions.append("c.category_id = ?")
        params.append(category_id)
    if pipeline_status:
        conditions.append("v.pipeline_status = ?")
        params.append(pipeline_status)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

    cursor = await db.execute(
        f"""
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.retry_count,
            v.created_at,
            v.viewed_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        {where_clause}
        ORDER BY v.{sort_column} {order_sql}
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )

    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def count_videos(
    db: aiosqlite.Connection,
    channel_id: str | None = None,
    category_id: int | None = None,
    pipeline_status: str | None = None,
) -> int:
    conditions: list[str] = []
    params: list[object] = []
    if channel_id:
        conditions.append("v.channel_id = ?")
        params.append(channel_id)
    if category_id is not None:
        conditions.append("c.category_id = ?")
        params.append(category_id)
    if pipeline_status:
        conditions.append("v.pipeline_status = ?")
        params.append(pipeline_status)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    cursor = await db.execute(
        f"""
        SELECT COUNT(1) AS cnt
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        {where_clause}
        """,
        tuple(params),
    )
    row = await cursor.fetchone()
    if row is None:
        return 0
    return int(row["cnt"] or 0)


async def get_video(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.retry_count,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE v.video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    raw = _row_to_dict(row)
    return _with_thumbnail_url(raw) if raw else None


async def list_videos_by_ids(
    db: aiosqlite.Connection,
    video_ids: list[str],
) -> list[dict[str, Any]]:
    normalized = [video_id for video_id in dict.fromkeys(video_ids) if str(video_id).strip()]
    if not normalized:
        return []

    placeholders = ",".join(["?"] * len(normalized))
    cursor = await db.execute(
        f"""
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.retry_count,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE v.video_id IN ({placeholders})
        """,
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def mark_video_viewed(db: aiosqlite.Connection, video_id: str) -> bool:
    cursor = await db.execute(
        "UPDATE videos SET viewed_at = datetime('now') WHERE video_id = ? AND viewed_at IS NULL",
        (video_id,),
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0


async def get_video_detail(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.retry_count,
            v.created_at,
            t.raw_text,
            t.language,
            t.source_type,
            a.title AS article_title,
            a.lead,
            a.body,
            a.fact_box,
            a.timestamps,
            a.llm_provider,
            a.llm_model,
            a.llm_reasoning_effort,
            a.llm_generated_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        LEFT JOIN transcripts t ON t.video_id = v.video_id
        LEFT JOIN articles a ON a.video_id = v.video_id
        WHERE v.video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    raw = _row_to_dict(row)
    return _with_thumbnail_url(raw) if raw else None


async def get_transcript(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT video_id, raw_text, language, source_type, created_at
        FROM transcripts
        WHERE video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_article(db: aiosqlite.Connection, video_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            video_id,
            title,
            lead,
            body,
            fact_box,
            timestamps,
            llm_provider,
            llm_model,
            llm_reasoning_effort,
            llm_generated_at,
            created_at
        FROM articles
        WHERE video_id = ?
        """,
        (video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def search_documents(db: aiosqlite.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT * FROM (
            SELECT
                'transcript' AS source,
                t.video_id AS video_id,
                v.title AS video_title,
                substr(t.raw_text, 1, 240) AS snippet,
                t.created_at AS created_at
            FROM transcripts_fts f
            JOIN transcripts t ON t.id = f.rowid
            JOIN videos v ON v.video_id = t.video_id
            WHERE transcripts_fts MATCH ?
            UNION ALL
            SELECT
                'article' AS source,
                a.video_id AS video_id,
                a.title AS video_title,
                substr(a.body, 1, 240) AS snippet,
                a.created_at AS created_at
            FROM articles_fts af
            JOIN articles a ON a.id = af.rowid
            WHERE articles_fts MATCH ?
        )
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (query, query, limit),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def list_queue_items(
    db: aiosqlite.Connection,
    statuses: tuple[str, ...],
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not statuses:
        return []
    safe_limit = max(1, min(500, int(limit)))
    placeholders = ", ".join("?" for _ in statuses)
    cursor = await db.execute(
        f"""
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.retry_count,
            v.transcript_retry_count,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE v.pipeline_status IN ({placeholders})
        ORDER BY
            CASE v.pipeline_status
                WHEN 'transcript_processing' THEN 0
                WHEN 'llm_processing' THEN 0
                WHEN 'transcript_pending' THEN 1
                WHEN 'llm_pending' THEN 1
                ELSE 2
            END,
            v.created_at ASC
        LIMIT ?
        """,
        (*statuses, safe_limit),
    )
    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def queue_status(db: aiosqlite.Connection) -> dict[str, int]:
    cursor = await db.execute(
        """
        SELECT
            pipeline_status,
            COUNT(1) AS cnt
        FROM videos
        GROUP BY pipeline_status
        """
    )
    rows = await cursor.fetchall()
    payload = {status: 0 for status in PIPELINE_STATUS_KEYS}
    payload["unknown_count"] = 0
    for row in rows:
        status = str(row["pipeline_status"] or "").strip()
        count = int(row["cnt"] or 0)
        if status in payload:
            payload[status] = count
        else:
            payload["unknown_count"] += count
    return payload


async def repair_orphan_llm_candidates(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'manual_review'
        WHERE pipeline_status IN ('llm_pending', 'llm_failed', 'llm_processing')
          AND NOT EXISTS (
              SELECT 1
              FROM transcripts t
              WHERE t.video_id = videos.video_id
          )
        """
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_video_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending',
            retry_count = 0
        WHERE video_id = ? AND pipeline_status IN ('llm_failed', 'manual_review')
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def requeue_done_video_for_manual_article_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending',
            retry_count = 0
        WHERE video_id = ?
          AND pipeline_status = 'done'
          AND EXISTS (
              SELECT 1
              FROM transcripts t
              WHERE t.video_id = videos.video_id
          )
        """,
        (video_id,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def pop_pending_transcript_videos(
    db: aiosqlite.Connection,
    limit: int = 3,
    *,
    lookahead: int | None = None,
    avoid_channel_id: str | None = None,
) -> list[dict[str, Any]]:
    safe_limit = max(1, int(limit))
    safe_lookahead = max(safe_limit, int(lookahead or safe_limit))
    blocked_channel = str(avoid_channel_id or "").strip()
    cursor = await db.execute(
        """
        SELECT
            video_id,
            channel_id,
            title,
            upload_time,
            transcript_retry_count,
            transcript_next_attempt_at,
            transcript_target_language
        FROM videos
        WHERE pipeline_status = 'transcript_pending'
          AND (
              transcript_next_attempt_at IS NULL
              OR transcript_next_attempt_at <= datetime('now')
          )
        ORDER BY upload_time DESC, created_at DESC
        LIMIT ?
        """,
        (safe_lookahead,),
    )
    rows = await cursor.fetchall()
    candidates = _rows_to_dicts(rows)
    if not blocked_channel:
        return candidates[:safe_limit]

    preferred = [row for row in candidates if str(row.get("channel_id") or "") != blocked_channel]
    if len(preferred) >= safe_limit:
        return preferred[:safe_limit]

    return (preferred + candidates)[:safe_limit]


async def mark_transcript_processing(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_processing'
        WHERE video_id = ?
          AND pipeline_status = 'transcript_pending'
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def save_transcript(
    db: aiosqlite.Connection,
    video_id: str,
    raw_text: str,
    language: str | None,
    source_type: str,
    thumbnail_path: str | None,
    *,
    force_llm_pending: bool = False,
) -> None:
    await db.execute(
        """
        INSERT INTO transcripts(video_id, raw_text, language, source_type)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            raw_text=excluded.raw_text,
            language=excluded.language,
            source_type=excluded.source_type
        """,
        (video_id, raw_text, language, source_type),
    )
    await db.execute(
        """
        UPDATE videos
        SET pipeline_status = CASE
                WHEN ? = 1 THEN 'llm_pending'
                WHEN processing_stage_snapshot = 'full' THEN 'llm_pending'
                ELSE 'transcript_done'
            END,
            processing_stage_snapshot = CASE lower(trim(coalesce(processing_stage_snapshot, '')))
                WHEN 'off' THEN 'off'
                WHEN 'transcript_only' THEN 'transcript_only'
                WHEN 'full' THEN 'full'
                ELSE 'full'
            END,
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_target_language = COALESCE(?, transcript_target_language),
            transcript_last_error = NULL,
            transcript_last_error_at = NULL,
            thumbnail_path = COALESCE(?, thumbnail_path)
        WHERE video_id = ?
        """,
        (1 if force_llm_pending else 0, language, thumbnail_path, video_id),
    )
    await db.commit()


async def update_video_thumbnail(
    db: aiosqlite.Connection,
    video_id: str,
    thumbnail_path: str | None,
) -> None:
    if not thumbnail_path:
        return
    await db.execute(
        """
        UPDATE videos
        SET thumbnail_path = ?
        WHERE video_id = ?
        """,
        (thumbnail_path, video_id),
    )
    await db.commit()


async def mark_no_subtitle(db: aiosqlite.Connection, video_id: str) -> None:
    await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'no_subtitle',
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_last_error = NULL,
            transcript_last_error_at = NULL
        WHERE video_id = ?
        """,
        (video_id,),
    )
    await db.commit()


async def schedule_transcript_retry(
    db: aiosqlite.Connection,
    video_id: str,
    delay_seconds: int,
    error_message: str | None = None,
) -> int:
    safe_delay = max(1, int(delay_seconds))
    safe_error = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_pending',
            transcript_retry_count = transcript_retry_count + 1,
            transcript_next_attempt_at = datetime('now', ?),
            transcript_last_error = ?,
            transcript_last_error_at = datetime('now')
        WHERE video_id = ?
          AND pipeline_status IN ('transcript_pending', 'transcript_processing')
        """,
        (f"+{safe_delay} seconds", safe_error, video_id),
    )
    await db.commit()
    return cursor.rowcount


async def defer_channel_transcript_retries(
    db: aiosqlite.Connection,
    channel_id: str,
    delay_seconds: int,
    exclude_video_id: str | None = None,
) -> int:
    safe_channel = str(channel_id).strip()
    if not safe_channel:
        return 0
    safe_delay = max(1, int(delay_seconds))
    safe_excluded = str(exclude_video_id or "").strip()
    cursor = await db.execute(
        """
        UPDATE videos
        SET transcript_next_attempt_at = CASE
            WHEN transcript_next_attempt_at IS NULL
                 OR transcript_next_attempt_at < datetime('now', ?)
            THEN datetime('now', ?)
            ELSE transcript_next_attempt_at
        END
        WHERE pipeline_status IN ('transcript_pending', 'transcript_processing')
          AND channel_id = ?
          AND (? = '' OR video_id != ?)
        """,
        (
            f"+{safe_delay} seconds",
            f"+{safe_delay} seconds",
            safe_channel,
            safe_excluded,
            safe_excluded,
        ),
    )
    await db.commit()
    return cursor.rowcount


async def mark_transcript_failed(
    db: aiosqlite.Connection,
    video_id: str,
    retry_count: int,
    error_message: str | None = None,
) -> int:
    safe_retry = max(0, int(retry_count))
    safe_error = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_failed',
            transcript_retry_count = ?,
            transcript_next_attempt_at = NULL,
            transcript_last_error = ?,
            transcript_last_error_at = datetime('now')
        WHERE video_id = ?
          AND pipeline_status IN ('transcript_pending', 'transcript_processing', 'transcript_failed', 'no_subtitle')
        """,
        (safe_retry, safe_error, video_id),
    )
    await db.commit()
    return cursor.rowcount


async def reset_transcript_for_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_pending',
            transcript_retry_count = 0,
            transcript_next_attempt_at = NULL,
            transcript_last_error = NULL,
            transcript_last_error_at = NULL
        WHERE video_id = ?
          AND pipeline_status IN ('transcript_failed', 'no_subtitle')
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def pop_llm_candidate(db: aiosqlite.Connection, max_retry_count: int) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.title,
            v.retry_count,
            t.raw_text
        FROM videos v
        JOIN transcripts t ON t.video_id = v.video_id
        WHERE v.pipeline_status IN ('llm_pending', 'llm_failed')
          AND v.retry_count < ?
        ORDER BY v.created_at ASC
        LIMIT 1
        """,
        (max_retry_count,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def mark_restructure_processing(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_processing'
        WHERE video_id = ?
          AND pipeline_status IN ('llm_pending', 'llm_failed')
        """,
        (video_id,),
    )
    await db.commit()
    return cursor.rowcount


async def save_article(
    db: aiosqlite.Connection,
    video_id: str,
    title: str,
    lead: str,
    body: str,
    fact_box: str | None,
    timestamps: str | None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_reasoning_effort: str | None = None,
    llm_generated_at: str | None = None,
) -> None:
    safe_provider = _normalize_article_provider(llm_provider)
    safe_model = _normalize_article_text_meta(llm_model)
    safe_reasoning_effort = _normalize_article_text_meta(llm_reasoning_effort, max_length=32)
    safe_generated_at = str(llm_generated_at or "").strip() or None

    await db.execute(
        """
        INSERT INTO articles(
            video_id,
            title,
            lead,
            body,
            fact_box,
            timestamps,
            llm_provider,
            llm_model,
            llm_reasoning_effort,
            llm_generated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
        ON CONFLICT(video_id) DO UPDATE SET
            title=excluded.title,
            lead=excluded.lead,
            body=excluded.body,
            fact_box=excluded.fact_box,
            timestamps=excluded.timestamps,
            llm_provider=excluded.llm_provider,
            llm_model=excluded.llm_model,
            llm_reasoning_effort=excluded.llm_reasoning_effort,
            llm_generated_at=excluded.llm_generated_at
        """,
        (
            video_id,
            title,
            lead,
            body,
            fact_box,
            timestamps,
            safe_provider,
            safe_model,
            safe_reasoning_effort,
            safe_generated_at,
        ),
    )
    await db.execute(
        "UPDATE videos SET pipeline_status = 'done' WHERE video_id = ?",
        (video_id,),
    )
    await db.commit()


async def mark_restructure_failed(
    db: aiosqlite.Connection,
    video_id: str,
    retry_count: int,
    max_retry_count: int,
) -> tuple[str, int]:
    next_retry = retry_count + 1
    next_status = "llm_failed" if next_retry < max_retry_count else "manual_review"
    cursor = await db.execute(
        """
        UPDATE videos
        SET retry_count = ?, pipeline_status = ?
        WHERE video_id = ?
          AND pipeline_status = 'llm_processing'
        """,
        (next_retry, next_status, video_id),
    )
    await db.commit()
    return next_status, int(cursor.rowcount or 0)


async def requeue_llm_pending_without_retry(db: aiosqlite.Connection, video_id: str) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending'
        WHERE video_id = ?
          AND pipeline_status = 'llm_processing'
        """,
        (video_id,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


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


async def ensure_llm_config_missing_alert(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos
        WHERE pipeline_status = 'llm_pending'
        """
    )
    row = await cursor.fetchone()
    pending_count = int((row["cnt"] if row is not None else 0) or 0)
    if pending_count <= 0:
        sent_raw = await get_setting(
            db,
            key=LLM_CONFIG_MISSING_ALERT_SENT_KEY,
            default="0",
        )
        if str(sent_raw or "0").strip() != "0":
            await set_setting(db, key=LLM_CONFIG_MISSING_ALERT_SENT_KEY, value="0")
        return False

    sent_raw = await get_setting(
        db,
        key=LLM_CONFIG_MISSING_ALERT_SENT_KEY,
        default="0",
    )
    if str(sent_raw or "0").strip() == "1":
        return False

    await create_system_alert(
        db,
        alert_type=ALERT_TYPE_LLM_CONFIG_MISSING,
        message="LLM runtime is not ready. llm_pending items are waiting.",
    )
    await set_setting(db, key=LLM_CONFIG_MISSING_ALERT_SENT_KEY, value="1")
    return True


async def clear_llm_config_missing_alert_flag(db: aiosqlite.Connection) -> None:
    sent_raw = await get_setting(
        db,
        key=LLM_CONFIG_MISSING_ALERT_SENT_KEY,
        default="0",
    )
    if str(sent_raw or "0").strip() == "0":
        return
    await set_setting(db, key=LLM_CONFIG_MISSING_ALERT_SENT_KEY, value="0")


async def ensure_llm_schema_invalid_alert(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos
        WHERE pipeline_status = 'llm_pending'
        """
    )
    row = await cursor.fetchone()
    pending_count = int((row["cnt"] if row is not None else 0) or 0)
    if pending_count <= 0:
        sent_raw = await get_setting(
            db,
            key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY,
            default="0",
        )
        if str(sent_raw or "0").strip() != "0":
            await set_setting(db, key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY, value="0")
        return False

    sent_raw = await get_setting(
        db,
        key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY,
        default="0",
    )
    if str(sent_raw or "0").strip() == "1":
        return False

    cursor = await db.execute(
        """
        SELECT 1
        FROM system_alerts
        WHERE alert_type = ?
          AND acknowledged_at IS NULL
        LIMIT 1
        """,
        (ALERT_TYPE_LLM_SCHEMA_INVALID,),
    )
    existing = await cursor.fetchone()
    if existing is not None:
        await set_setting(db, key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY, value="1")
        return False

    await create_system_alert(
        db,
        alert_type=ALERT_TYPE_LLM_SCHEMA_INVALID,
        message="LLM output schema is incompatible. llm_pending items are waiting.",
    )
    await set_setting(db, key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY, value="1")
    return True


async def clear_llm_schema_invalid_alert_flag(db: aiosqlite.Connection) -> None:
    sent_raw = await get_setting(
        db,
        key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY,
        default="0",
    )
    if str(sent_raw or "0").strip() == "0":
        return
    await set_setting(db, key=LLM_SCHEMA_INVALID_ALERT_SENT_KEY, value="0")


async def set_llm_runtime_issue(
    db: aiosqlite.Connection,
    *,
    code: str,
    message: str,
    seen_at: str | None = None,
) -> None:
    normalized_code = str(code or "").strip()
    normalized_message = str(message or "").strip()
    normalized_seen_at = str(seen_at or "").strip()
    if not normalized_seen_at:
        normalized_seen_at = datetime.now(timezone.utc).isoformat()
    await set_setting(db, key=LLM_RUNTIME_LAST_CODE_KEY, value=normalized_code)
    await set_setting(db, key=LLM_RUNTIME_LAST_MESSAGE_KEY, value=normalized_message)
    await set_setting(db, key=LLM_RUNTIME_LAST_SEEN_AT_KEY, value=normalized_seen_at)


async def clear_llm_runtime_issue(db: aiosqlite.Connection) -> None:
    await set_setting(db, key=LLM_RUNTIME_LAST_CODE_KEY, value="")
    await set_setting(db, key=LLM_RUNTIME_LAST_MESSAGE_KEY, value="")
    await set_setting(db, key=LLM_RUNTIME_LAST_SEEN_AT_KEY, value="")


async def get_llm_runtime_issue(db: aiosqlite.Connection) -> dict[str, str]:
    code = str(await get_setting(db, key=LLM_RUNTIME_LAST_CODE_KEY, default="") or "").strip()
    message = str(await get_setting(db, key=LLM_RUNTIME_LAST_MESSAGE_KEY, default="") or "").strip()
    seen_at = str(await get_setting(db, key=LLM_RUNTIME_LAST_SEEN_AT_KEY, default="") or "").strip()
    return {
        "code": code,
        "message": message,
        "seen_at": seen_at,
    }


async def count_llm_pending_videos(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos v
        WHERE v.pipeline_status = 'llm_pending'
        """
    )
    row = await cursor.fetchone()
    return int((row["cnt"] if row is not None else 0) or 0)


async def get_transcript_request_header_overrides(db: aiosqlite.Connection) -> dict[str, str]:
    raw = await get_setting(
        db,
        key=TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY,
        default="{}",
    )
    text = str(raw or "").strip() or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    overrides: dict[str, str] = {}
    for key, value in parsed.items():
        overrides[str(key).strip()] = str(value).strip()
    return overrides


async def save_transcript_request_header_overrides(
    db: aiosqlite.Connection,
    overrides: dict[str, str],
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in overrides.items():
        normalized[str(key).strip()] = str(value).strip()
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    await set_setting(
        db,
        key=TRANSCRIPT_REQUEST_HEADERS_OVERRIDES_KEY,
        value=payload,
    )
    return await get_transcript_request_header_overrides(db)


async def acquire_transcript_worker_lease(
    db: aiosqlite.Connection,
    owner_id: str,
    ttl_seconds: int,
) -> bool:
    safe_owner = str(owner_id).strip()
    if not safe_owner:
        return False
    ttl = max(5, int(ttl_seconds))
    now = datetime.now(timezone.utc)
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
        current_until = _parse_datetime_setting(str(until_row["value"] if until_row is not None else ""))

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
        lease_until = datetime.now(timezone.utc) + timedelta(seconds=ttl)
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
    adaptive_raw = await get_setting(
        db,
        "transcript_guard_adaptive_factor",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_adaptive_factor"],
    )
    cooldown_raw = await get_setting(
        db,
        "transcript_guard_cooldown_until",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_cooldown_until"],
    )
    hard_raw = await get_setting(
        db,
        "transcript_guard_consecutive_hard_errors",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_consecutive_hard_errors"],
    )
    success_raw = await get_setting(
        db,
        "transcript_guard_consecutive_successes",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_consecutive_successes"],
    )
    breaker_state_raw = await get_setting(
        db,
        "transcript_guard_breaker_state",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_breaker_state"],
    )
    probe_raw = await get_setting(
        db,
        "transcript_guard_half_open_probe_remaining",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_half_open_probe_remaining"],
    )
    last_channel_raw = await get_setting(
        db,
        "transcript_guard_last_channel_id",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_last_channel_id"],
    )
    last_channel_attempt_raw = await get_setting(
        db,
        "transcript_guard_last_channel_attempt_at",
        TRANSCRIPT_GUARD_DEFAULTS["transcript_guard_last_channel_attempt_at"],
    )

    cooldown_until = str(cooldown_raw or "").strip() or None
    breaker_state = str(breaker_state_raw or "closed").strip().lower() or "closed"
    if breaker_state not in {"closed", "open", "half_open"}:
        breaker_state = "closed"
    last_channel_attempt = _parse_datetime_setting(str(last_channel_attempt_raw or ""))
    return {
        "adaptive_factor": _parse_float_setting(adaptive_raw, default=1.0, min_value=1.0, max_value=64.0),
        "cooldown_until": cooldown_until,
        "consecutive_hard_errors": _parse_int_setting(hard_raw, default=0, min_value=0, max_value=100000),
        "consecutive_successes": _parse_int_setting(success_raw, default=0, min_value=0, max_value=100000),
        "breaker_state": breaker_state,
        "half_open_probe_remaining": _parse_int_setting(probe_raw, default=1, min_value=1, max_value=1000),
        "last_channel_id": str(last_channel_raw or "").strip() or None,
        "last_channel_attempt_at": last_channel_attempt.isoformat() if last_channel_attempt else None,
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


async def get_worker_settings(db: aiosqlite.Connection) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for worker, key in WORKER_SETTING_KEY_MAP.items():
        default = WORKER_SETTING_DEFAULTS.get(worker, True)
        raw = await get_setting(db, key=key, default="true" if default else "false")
        result[worker] = _parse_bool_setting(raw, default=default)
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
    return _parse_bool_setting(raw, default=default)


async def get_policy_settings(db: aiosqlite.Connection) -> dict[str, int | str]:
    lookback_raw = await get_setting(
        db,
        key=RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY,
        default=str(RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT),
    )
    retention_raw = await get_setting(
        db,
        key=RETENTION_DAYS_KEY,
        default=str(RETENTION_DAYS_DEFAULT),
    )
    feed_mode_raw = await get_setting(db, key=RSS_FEED_MODE_KEY, default=RSS_FEED_MODE_DEFAULT)
    feed_mode = str(feed_mode_raw).strip().lower() if feed_mode_raw else RSS_FEED_MODE_DEFAULT
    if feed_mode not in RSS_FEED_MODE_OPTIONS:
        feed_mode = RSS_FEED_MODE_DEFAULT
    return {
        "rss_bootstrap_lookback_days": _parse_int_setting(
            lookback_raw,
            default=RSS_BOOTSTRAP_LOOKBACK_DAYS_DEFAULT,
            min_value=1,
            max_value=3650,
        ),
        "retention_days": _parse_int_setting(
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
    lookback_value = current["rss_bootstrap_lookback_days"]
    retention_value = current["retention_days"]

    if rss_bootstrap_lookback_days is not None:
        lookback_value = _parse_int_setting(
            str(rss_bootstrap_lookback_days),
            default=lookback_value,
            min_value=1,
            max_value=3650,
        )
        await set_setting(db, key=RSS_BOOTSTRAP_LOOKBACK_DAYS_KEY, value=str(lookback_value))

    if retention_days is not None:
        retention_value = _parse_int_setting(
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
    raw = await get_setting(
        db,
        key=VIDEOS_PER_PAGE_KEY,
        default=str(VIDEOS_PER_PAGE_DEFAULT),
    )
    return _parse_int_setting(
        raw,
        default=VIDEOS_PER_PAGE_DEFAULT,
        min_value=1,
        max_value=100,
    )


async def set_videos_per_page_setting(db: aiosqlite.Connection, value: int) -> int:
    normalized = _parse_int_setting(
        str(value),
        default=VIDEOS_PER_PAGE_DEFAULT,
        min_value=1,
        max_value=100,
    )
    await set_setting(db, key=VIDEOS_PER_PAGE_KEY, value=str(normalized))
    return normalized


async def get_llm_settings(db: aiosqlite.Connection) -> dict[str, Any]:
    primary_raw = await get_setting(
        db,
        key=LLM_PROVIDER_PRIMARY_KEY,
        default=LLM_PROVIDER_PRIMARY_DEFAULT,
    )
    fallback_raw = await get_setting(
        db,
        key=LLM_PROVIDER_FALLBACK_KEY,
        default=LLM_PROVIDER_FALLBACK_DEFAULT,
    )
    prompt_raw = await get_setting(
        db,
        key=LLM_PROMPT_TEMPLATE_KEY,
        default="",
    )
    model_claude_raw = await get_setting(
        db,
        key=LLM_MODEL_CLAUDE_KEY,
        default="",
    )
    reasoning_effort_codex_raw = await get_setting(
        db,
        key=LLM_REASONING_EFFORT_CODEX_KEY,
        default="",
    )
    reasoning_effort_claude_raw = await get_setting(
        db,
        key=LLM_REASONING_EFFORT_CLAUDE_KEY,
        default="",
    )

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
        model = _validate_llm_model_settings({"claude": model_claude_raw})
    except ValueError:
        model = {
            "codex": LLM_CODEX_MODEL_FIXED,
            "claude": "",
        }
    try:
        reasoning_effort_codex = _normalize_reasoning_effort(reasoning_effort_codex_raw)
    except ValueError:
        reasoning_effort_codex = ""
    try:
        reasoning_effort_claude = _normalize_reasoning_effort(reasoning_effort_claude_raw)
    except ValueError:
        reasoning_effort_claude = ""
    reasoning_effort = {
        "codex": reasoning_effort_codex,
        "claude": reasoning_effort_claude,
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
    next_model_claude = str(current_model.get("claude", ""))
    next_reasoning_effort_codex = str(current_reasoning_effort.get("codex", ""))
    next_reasoning_effort_claude = str(current_reasoning_effort.get("claude", ""))

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
            "codex": LLM_CODEX_MODEL_FIXED,
            "claude": next_model_claude,
        }
        next_model_payload.update(
            {
                key: value
                for key, value in llm_model.items()
                if key in {"codex", "claude"}
            }
        )
        validated_model = _validate_llm_model_settings(next_model_payload)
        next_model_claude = validated_model["claude"]
    if llm_reasoning_effort is not None:
        next_effort_payload = {
            "codex": next_reasoning_effort_codex,
            "claude": next_reasoning_effort_claude,
        }
        next_effort_payload.update(
            {
                key: value
                for key, value in llm_reasoning_effort.items()
                if key in {"codex", "claude"}
            }
        )
        validated_effort = _validate_llm_reasoning_effort_settings(next_effort_payload)
        next_reasoning_effort_codex = validated_effort["codex"]
        next_reasoning_effort_claude = validated_effort["claude"]

    next_settings: dict[str, Any] = {
        "provider_primary": next_primary,
        "provider_fallback": next_fallback,
        "prompt_template": next_prompt,
        "llm_model": {
            "codex": LLM_CODEX_MODEL_FIXED,
            "claude": next_model_claude,
        },
        "llm_reasoning_effort": {
            "codex": next_reasoning_effort_codex,
            "claude": next_reasoning_effort_claude,
        },
    }
    if not persist:
        return next_settings

    await set_setting(db, key=LLM_PROVIDER_PRIMARY_KEY, value=next_primary)
    await set_setting(db, key=LLM_PROVIDER_FALLBACK_KEY, value=next_fallback)
    await set_setting(db, key=LLM_PROMPT_TEMPLATE_KEY, value=next_prompt)
    await set_setting(db, key=LLM_MODEL_CLAUDE_KEY, value=next_model_claude)
    await set_setting(db, key=LLM_REASONING_EFFORT_CODEX_KEY, value=next_reasoning_effort_codex)
    await set_setting(db, key=LLM_REASONING_EFFORT_CLAUDE_KEY, value=next_reasoning_effort_claude)
    return next_settings


async def create_system_alert(
    db: aiosqlite.Connection,
    alert_type: str,
    message: str,
    channel_id: str | None = None,
    channel_name: str | None = None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO system_alerts(alert_type, channel_id, channel_name, message)
        VALUES (?, ?, ?, ?)
        """,
        (alert_type, channel_id, channel_name, message),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def list_unacknowledged_alerts(
    db: aiosqlite.Connection,
    limit: int = 5,
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT id, alert_type, channel_id, channel_name, message, created_at
        FROM system_alerts
        WHERE acknowledged_at IS NULL
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, limit),),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def list_unacknowledged_alert_groups(
    db: aiosqlite.Connection,
    limit: int = 5,
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT id, alert_type, channel_id, channel_name, message, created_at
        FROM system_alerts
        WHERE acknowledged_at IS NULL
        ORDER BY created_at DESC, id DESC
        """
    )
    rows = await cursor.fetchall()
    alerts = _rows_to_dicts(rows)

    grouped: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        alert_type = str(alert.get("alert_type") or "").strip() or "unknown"
        group = grouped.get(alert_type)
        if group is None:
            group = {
                "alert_type": alert_type,
                "count": 0,
                "latest_created_at": str(alert.get("created_at") or ""),
                "members": [],
            }
            grouped[alert_type] = group

        group["count"] = int(group["count"]) + 1
        group["members"].append(
            {
                "id": alert["id"],
                "channel_id": alert["channel_id"],
                "channel_name": alert["channel_name"],
                "message": alert["message"],
                "created_at": alert["created_at"],
            }
        )

    groups = list(grouped.values())
    groups.sort(
        key=lambda item: (str(item.get("latest_created_at") or ""), str(item.get("alert_type") or "")),
        reverse=True,
    )
    return groups[: max(1, limit)]


async def acknowledge_alert(db: aiosqlite.Connection, alert_id: int) -> int:
    cursor = await db.execute(
        """
        UPDATE system_alerts
        SET acknowledged_at = datetime('now')
        WHERE id = ?
          AND acknowledged_at IS NULL
        """,
        (alert_id,),
    )
    await db.commit()
    return cursor.rowcount


async def acknowledge_alerts_by_type(db: aiosqlite.Connection, alert_type: str) -> int:
    cursor = await db.execute(
        """
        UPDATE system_alerts
        SET acknowledged_at = datetime('now')
        WHERE alert_type = ?
          AND acknowledged_at IS NULL
        """,
        (alert_type,),
    )
    await db.commit()
    return cursor.rowcount


async def count_retention_expired_videos(db: aiosqlite.Connection, retention_days: int) -> int:
    modifier = f"-{max(1, int(retention_days))} days"
    cursor = await db.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM videos
        WHERE datetime(upload_time) <= datetime('now', ?)
        """,
        (modifier,),
    )
    row = await cursor.fetchone()
    return int((row["cnt"] if row else 0) or 0)


async def list_retention_expired_video_ids(db: aiosqlite.Connection, retention_days: int) -> list[str]:
    modifier = f"-{max(1, int(retention_days))} days"
    cursor = await db.execute(
        """
        SELECT video_id
        FROM videos
        WHERE datetime(upload_time) <= datetime('now', ?)
        ORDER BY datetime(upload_time) ASC, created_at ASC
        """,
        (modifier,),
    )
    rows = await cursor.fetchall()
    return [str(row["video_id"]) for row in rows]


async def list_retention_expired_videos(db: aiosqlite.Connection, retention_days: int) -> list[dict[str, Any]]:
    modifier = f"-{max(1, int(retention_days))} days"
    cursor = await db.execute(
        """
        SELECT
            v.video_id,
            v.channel_id,
            c.channel_name AS channel_name,
            v.title,
            v.upload_time,
            v.thumbnail_path,
            v.pipeline_status,
            v.created_at
        FROM videos v
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE datetime(v.upload_time) <= datetime('now', ?)
        ORDER BY datetime(v.upload_time) ASC, v.created_at ASC
        """,
        (modifier,),
    )
    rows = await cursor.fetchall()
    return [_with_thumbnail_url(item) for item in _rows_to_dicts(rows)]


async def delete_videos_by_ids(
    db: aiosqlite.Connection,
    video_ids: list[str],
) -> dict[str, Any]:
    normalized = [video_id for video_id in dict.fromkeys(video_ids) if video_id]
    if not normalized:
        return {"deleted": 0, "thumbnail_paths": []}

    placeholders = ",".join(["?"] * len(normalized))

    cursor = await db.execute(
        f"SELECT thumbnail_path FROM videos WHERE video_id IN ({placeholders})",
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    thumbnail_paths = [str(row["thumbnail_path"]) for row in rows if row["thumbnail_path"]]

    await db.execute(
        f"DELETE FROM articles WHERE video_id IN ({placeholders})",
        tuple(normalized),
    )
    await db.execute(
        f"DELETE FROM transcripts WHERE video_id IN ({placeholders})",
        tuple(normalized),
    )
    cursor = await db.execute(
        f"DELETE FROM videos WHERE video_id IN ({placeholders})",
        tuple(normalized),
    )
    await db.commit()
    return {"deleted": int(cursor.rowcount or 0), "thumbnail_paths": thumbnail_paths}


async def delete_channels_with_related_data(
    db: aiosqlite.Connection,
    channel_ids: list[str],
) -> dict[str, Any]:
    normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
    if not normalized:
        return {"deleted_channels": 0, "deleted_videos": 0, "thumbnail_paths": []}

    placeholders = ",".join(["?"] * len(normalized))

    cursor = await db.execute(
        f"SELECT thumbnail_path FROM videos WHERE channel_id IN ({placeholders})",
        tuple(normalized),
    )
    rows = await cursor.fetchall()
    thumbnail_paths = [str(row["thumbnail_path"]) for row in rows if row["thumbnail_path"]]

    await db.execute(
        f"""
        DELETE FROM articles
        WHERE video_id IN (
            SELECT video_id FROM videos WHERE channel_id IN ({placeholders})
        )
        """,
        tuple(normalized),
    )
    await db.execute(
        f"""
        DELETE FROM transcripts
        WHERE video_id IN (
            SELECT video_id FROM videos WHERE channel_id IN ({placeholders})
        )
        """,
        tuple(normalized),
    )
    videos_cursor = await db.execute(
        f"DELETE FROM videos WHERE channel_id IN ({placeholders})",
        tuple(normalized),
    )
    channels_cursor = await db.execute(
        f"DELETE FROM channels WHERE channel_id IN ({placeholders})",
        tuple(normalized),
    )
    await db.commit()

    return {
        "deleted_channels": int(channels_cursor.rowcount or 0),
        "deleted_videos": int(videos_cursor.rowcount or 0),
        "thumbnail_paths": thumbnail_paths,
    }


async def get_manual_article_job(
    db: aiosqlite.Connection,
    job_id: int,
) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            j.id,
            j.video_id,
            j.status,
            j.error_message,
            j.requested_at,
            j.started_at,
            j.finished_at,
            j.updated_at,
            v.pipeline_status,
            v.transcript_retry_count,
            v.transcript_target_language,
            EXISTS(
                SELECT 1
                FROM transcripts t
                WHERE t.video_id = j.video_id
            ) AS has_transcript
        FROM manual_article_jobs j
        LEFT JOIN videos v ON v.video_id = j.video_id
        WHERE j.id = ?
        """,
        (int(job_id),),
    )
    row = await cursor.fetchone()
    payload = _row_to_dict(row)
    if payload is None:
        return None
    payload["has_transcript"] = bool(payload.get("has_transcript"))
    return payload


async def get_active_manual_article_job_for_video(
    db: aiosqlite.Connection,
    video_id: str,
) -> dict[str, Any] | None:
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return None
    cursor = await db.execute(
        """
        SELECT id
        FROM manual_article_jobs
        WHERE video_id = ?
          AND status IN ('pending', 'running')
        ORDER BY requested_at DESC, id DESC
        LIMIT 1
        """,
        (normalized_video_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return await get_manual_article_job(db, int(row["id"]))


async def enqueue_manual_article_jobs(
    db: aiosqlite.Connection,
    video_ids: list[str],
) -> dict[str, Any]:
    summary: dict[str, list[str]] = {
        "new": [],
        "retry": [],
        "skip": [],
        "failed": [],
    }
    requested_ids = list(video_ids or [])
    normalized_ids: list[str] = []
    seen_ids: set[str] = set()

    for raw_video_id in requested_ids:
        normalized = str(raw_video_id or "").strip()
        if not normalized:
            summary["failed"].append("")
            continue
        if normalized in seen_ids:
            summary["skip"].append(normalized)
            continue
        seen_ids.add(normalized)
        normalized_ids.append(normalized)

    if not normalized_ids:
        payload = {
            "new": summary["new"],
            "retry": summary["retry"],
            "skip": summary["skip"],
            "failed": summary["failed"],
            "new_count": len(summary["new"]),
            "retry_count": len(summary["retry"]),
            "skip_count": len(summary["skip"]),
            "failed_count": len(summary["failed"]),
        }
        logger.info(
            "event=manual_article.enqueue requested=%s new=%s retry=%s skip=%s failed=%s",
            len(requested_ids),
            payload["new_count"],
            payload["retry_count"],
            payload["skip_count"],
            payload["failed_count"],
            extra={"event": "manual_article.enqueue"},
        )
        return payload

    placeholders = ",".join(["?"] * len(normalized_ids))
    videos_cursor = await db.execute(
        f"""
        SELECT
            video_id,
            pipeline_status
        FROM videos
        WHERE video_id IN ({placeholders})
        """,
        tuple(normalized_ids),
    )
    video_rows = await videos_cursor.fetchall()
    video_status_map = {str(row["video_id"]): str(row["pipeline_status"] or "").strip().lower() for row in video_rows}

    active_cursor = await db.execute(
        f"""
        SELECT DISTINCT video_id
        FROM manual_article_jobs
        WHERE video_id IN ({placeholders})
          AND status IN ('pending', 'running')
        """,
        tuple(normalized_ids),
    )
    active_rows = await active_cursor.fetchall()
    active_video_ids = {str(row["video_id"]) for row in active_rows}

    created_count = 0
    for video_id in normalized_ids:
        pipeline_status = video_status_map.get(video_id)
        if pipeline_status is None:
            summary["failed"].append(video_id)
            continue

        if video_id in active_video_ids:
            summary["skip"].append(video_id)
            continue

        if pipeline_status in MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES:
            summary["skip"].append(video_id)
            continue

        category = "retry" if pipeline_status in MANUAL_ARTICLE_ENQUEUE_RETRY_PIPELINE_STATUSES else "new"
        try:
            await db.execute(
                """
                INSERT INTO manual_article_jobs(
                    video_id,
                    status,
                    requested_at,
                    updated_at
                )
                VALUES (?, 'pending', datetime('now'), datetime('now'))
                """,
                (video_id,),
            )
            summary[category].append(video_id)
            created_count += 1
        except sqlite3.IntegrityError:
            summary["skip"].append(video_id)

    if created_count > 0:
        await db.commit()

    payload = {
        "new": summary["new"],
        "retry": summary["retry"],
        "skip": summary["skip"],
        "failed": summary["failed"],
        "new_count": len(summary["new"]),
        "retry_count": len(summary["retry"]),
        "skip_count": len(summary["skip"]),
        "failed_count": len(summary["failed"]),
    }
    logger.info(
        "event=manual_article.enqueue requested=%s new=%s retry=%s skip=%s failed=%s",
        len(requested_ids),
        payload["new_count"],
        payload["retry_count"],
        payload["skip_count"],
        payload["failed_count"],
        extra={"event": "manual_article.enqueue"},
    )
    return payload


async def claim_next_manual_article_job(db: aiosqlite.Connection) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT id
        FROM manual_article_jobs
        WHERE status = 'pending'
        ORDER BY requested_at ASC, id ASC
        LIMIT 1
        """
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    job_id = int(row["id"])
    updated = await db.execute(
        """
        UPDATE manual_article_jobs
        SET
            status = 'running',
            started_at = COALESCE(started_at, datetime('now')),
            finished_at = NULL,
            error_message = NULL,
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'pending'
        """,
        (job_id,),
    )
    if int(updated.rowcount or 0) == 0:
        return None

    await db.commit()
    claimed = await get_manual_article_job(db, job_id)
    if claimed is not None:
        logger.info(
            "event=manual_article.claim job_id=%s video_id=%s",
            claimed["id"],
            claimed["video_id"],
            extra={"event": "manual_article.claim"},
        )
    return claimed


async def mark_manual_article_job_succeeded(db: aiosqlite.Connection, *, job_id: int) -> int:
    cursor = await db.execute(
        """
        UPDATE manual_article_jobs
        SET
            status = 'succeeded',
            error_message = NULL,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (int(job_id),),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_manual_article_job_failed(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    error_message: str,
) -> int:
    normalized_error = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE manual_article_jobs
        SET
            status = 'failed',
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (normalized_error, int(job_id)),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def mark_manual_article_job_skipped(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    reason: str | None = None,
) -> int:
    normalized_reason = _normalize_error_message(reason)
    cursor = await db.execute(
        """
        UPDATE manual_article_jobs
        SET
            status = 'skipped',
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (normalized_reason or None, int(job_id)),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def recover_stuck_manual_article_jobs(
    db: aiosqlite.Connection,
    *,
    stale_after_seconds: int | None = None,
    exclude_job_ids: list[int] | None = None,
) -> int:
    where_clauses: list[str] = ["status = 'running'"]
    query_params: list[Any] = []
    recovery_mode = "startup"
    error_message = "manual article worker interrupted (app restart/shutdown)"

    safe_stale_after_seconds: int | None = None
    if stale_after_seconds is not None:
        safe_stale_after_seconds = max(1, int(stale_after_seconds))
        threshold = f"-{safe_stale_after_seconds} seconds"
        where_clauses.append("COALESCE(started_at, updated_at, requested_at) <= datetime('now', ?)")
        where_clauses.append("updated_at <= datetime('now', ?)")
        query_params.extend([threshold, threshold])
        recovery_mode = "runtime"
        error_message = f"manual article worker stale timeout exceeded ({safe_stale_after_seconds}s)"

    normalized_exclude_ids: list[int] = []
    for raw_job_id in exclude_job_ids or []:
        try:
            parsed_id = int(raw_job_id)
        except (TypeError, ValueError):
            continue
        if parsed_id > 0:
            normalized_exclude_ids.append(parsed_id)
    normalized_exclude_ids = sorted(set(normalized_exclude_ids))
    if normalized_exclude_ids:
        placeholders = ",".join(["?"] * len(normalized_exclude_ids))
        where_clauses.append(f"id NOT IN ({placeholders})")
        query_params.extend(normalized_exclude_ids)

    cursor = await db.execute(
        f"""
        UPDATE manual_article_jobs
        SET
            status = 'failed',
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE {' AND '.join(where_clauses)}
        """,
        (error_message, *query_params),
    )
    await db.commit()
    recovered = int(cursor.rowcount or 0)
    if recovered > 0:
        logger.info(
            "event=manual_article.recover mode=%s recovered=%s stale_after_seconds=%s",
            recovery_mode,
            recovered,
            safe_stale_after_seconds if safe_stale_after_seconds is not None else "-",
            extra={"event": "manual_article.recover"},
        )
    return recovered


async def ensure_video_llm_pending_for_manual_article(db: aiosqlite.Connection, video_id: str) -> int:
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return 0
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending',
            retry_count = 0
        WHERE video_id = ?
          AND pipeline_status NOT IN ('llm_pending', 'llm_processing', 'done')
        """,
        (normalized_video_id,),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def force_mark_video_transcript_failed_for_manual_article(
    db: aiosqlite.Connection,
    *,
    video_id: str,
    retry_count: int,
    error_message: str,
) -> int:
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return 0
    safe_retry_count = max(1, int(retry_count))
    safe_error_message = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'transcript_failed',
            transcript_retry_count = ?,
            transcript_next_attempt_at = NULL,
            transcript_last_error = ?,
            transcript_last_error_at = datetime('now')
        WHERE video_id = ?
        """,
        (safe_retry_count, safe_error_message, normalized_video_id),
    )
    await db.commit()
    return int(cursor.rowcount or 0)


async def get_download_default_settings(
    db: aiosqlite.Connection,
    *,
    default_output_dir: str | None = None,
) -> dict[str, Any]:
    quality_raw = await get_setting(
        db,
        key=DOWNLOAD_DEFAULT_QUALITY_KEY,
        default=DOWNLOAD_QUALITY_DEFAULT,
    )
    overwrite_raw = await get_setting(
        db,
        key=DOWNLOAD_DEFAULT_OVERWRITE_KEY,
        default="false",
    )
    default_dir_raw = str(default_output_dir or DOWNLOAD_OUTPUT_DIR_DEFAULT).strip() or DOWNLOAD_OUTPUT_DIR_DEFAULT
    output_dir_raw = await get_setting(
        db,
        key=DOWNLOAD_DEFAULT_OUTPUT_DIR_KEY,
        default=default_dir_raw,
    )
    output_dir_candidate = str(output_dir_raw or "").strip() or default_dir_raw
    output_dir_path = Path(output_dir_candidate).expanduser()
    try:
        output_dir_resolved = output_dir_path.resolve(strict=False)
    except OSError:
        output_dir_resolved = Path(default_dir_raw).expanduser().resolve(strict=False)
    return {
        "quality": _normalize_download_quality(quality_raw),
        "overwrite": _parse_bool_setting(overwrite_raw, default=False),
        "output_dir": str(output_dir_resolved),
    }


async def set_download_default_settings(
    db: aiosqlite.Connection,
    *,
    quality: str | None = None,
    overwrite: bool | None = None,
    output_dir: str | None = None,
    default_output_dir: str | None = None,
) -> dict[str, Any]:
    current = await get_download_default_settings(
        db,
        default_output_dir=default_output_dir,
    )
    next_quality = _normalize_download_quality(quality) if quality is not None else str(current["quality"])
    next_overwrite = bool(overwrite) if overwrite is not None else bool(current["overwrite"])
    next_output_dir = str(output_dir or "").strip() if output_dir is not None else str(current["output_dir"])
    validation = validate_download_output_dir(
        next_output_dir,
        require_absolute=True,
        require_existing=True,
    )
    if not validation.ok:
        raise ValueError(validation.error_code or "download_path_invalid")
    await set_setting(db, key=DOWNLOAD_DEFAULT_QUALITY_KEY, value=next_quality)
    await set_setting(db, key=DOWNLOAD_DEFAULT_OVERWRITE_KEY, value="true" if next_overwrite else "false")
    await set_setting(db, key=DOWNLOAD_DEFAULT_OUTPUT_DIR_KEY, value=validation.normalized_path)
    return await get_download_default_settings(
        db,
        default_output_dir=default_output_dir,
    )


async def _insert_download_event(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    event_type: str,
    error_code: str | None = None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO download_events(job_id, event_type, error_code)
        VALUES (?, ?, ?)
        """,
        (job_id, event_type, str(error_code or "").strip() or None),
    )
    return int(cursor.lastrowid or 0)


async def get_download_job(
    db: aiosqlite.Connection,
    job_id: int,
) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT
            id,
            video_id,
            video_title,
            status,
            quality,
            overwrite,
            target_dir,
            attempt_count,
            output_path,
            file_size_bytes,
            error_code,
            error_message,
            requested_at,
            started_at,
            finished_at,
            updated_at
        FROM download_jobs
        WHERE id = ?
        """,
        (int(job_id),),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_active_download_job_for_video(
    db: aiosqlite.Connection,
    video_id: str,
) -> dict[str, Any] | None:
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return None
    cursor = await db.execute(
        """
        SELECT
            id,
            video_id,
            video_title,
            status,
            quality,
            overwrite,
            target_dir,
            attempt_count,
            output_path,
            file_size_bytes,
            error_code,
            error_message,
            requested_at,
            started_at,
            finished_at,
            updated_at
        FROM download_jobs
        WHERE video_id = ?
          AND status IN ('pending', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        (normalized_video_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def create_download_job(
    db: aiosqlite.Connection,
    *,
    video_id: str,
    video_title: str,
    quality: str,
    overwrite: bool,
    target_dir: str,
) -> dict[str, Any]:
    normalized_video_id = str(video_id).strip()
    normalized_title = str(video_title).strip() or normalized_video_id
    normalized_quality = _normalize_download_quality(quality)
    normalized_overwrite = 1 if bool(overwrite) else 0
    normalized_target_dir = str(target_dir).strip()

    existing = await get_active_download_job_for_video(db, normalized_video_id)
    if existing is not None:
        return {"created": False, "duplicate": True, "job": existing}

    try:
        cursor = await db.execute(
            """
            INSERT INTO download_jobs(
                video_id,
                video_title,
                status,
                quality,
                overwrite,
                target_dir,
                attempt_count,
                requested_at,
                updated_at
            )
            VALUES (?, ?, 'pending', ?, ?, ?, 1, datetime('now'), datetime('now'))
            """,
            (
                normalized_video_id,
                normalized_title,
                normalized_quality,
                normalized_overwrite,
                normalized_target_dir,
            ),
        )
        job_id = int(cursor.lastrowid or 0)
        await _insert_download_event(
            db,
            job_id=job_id,
            event_type="enqueued",
        )
        await db.commit()
        created = await get_download_job(db, job_id)
        return {"created": True, "duplicate": False, "job": created}
    except sqlite3.IntegrityError:
        existing = await get_active_download_job_for_video(db, normalized_video_id)
        if existing is not None:
            return {"created": False, "duplicate": True, "job": existing}
        raise


async def claim_next_download_job(db: aiosqlite.Connection) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT id
        FROM download_jobs
        WHERE status = 'pending'
        ORDER BY requested_at ASC, id ASC
        LIMIT 1
        """
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    job_id = int(row["id"])
    updated = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'running',
            started_at = COALESCE(started_at, datetime('now')),
            updated_at = datetime('now'),
            error_code = NULL,
            error_message = NULL
        WHERE id = ?
          AND status = 'pending'
        """,
        (job_id,),
    )
    if int(updated.rowcount or 0) == 0:
        return None

    await _insert_download_event(
        db,
        job_id=job_id,
        event_type="started",
    )
    await db.commit()
    return await get_download_job(db, job_id)


async def mark_download_job_succeeded(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    output_path: str | None,
    file_size_bytes: int | None,
) -> int:
    cursor = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'succeeded',
            output_path = ?,
            file_size_bytes = ?,
            error_code = NULL,
            error_message = NULL,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (
            str(output_path or "").strip() or None,
            int(file_size_bytes) if file_size_bytes is not None else None,
            int(job_id),
        ),
    )
    rowcount = int(cursor.rowcount or 0)
    if rowcount > 0:
        await _insert_download_event(
            db,
            job_id=int(job_id),
            event_type="succeeded",
        )
    await db.commit()
    return rowcount


async def mark_download_job_failed(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    error_code: str,
    error_message: str,
) -> int:
    normalized_error_code = str(error_code or "").strip().lower() or "unknown"
    normalized_error_message = _normalize_error_message(error_message)
    cursor = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'failed',
            error_code = ?,
            error_message = ?,
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
        """,
        (
            normalized_error_code,
            normalized_error_message,
            int(job_id),
        ),
    )
    rowcount = int(cursor.rowcount or 0)
    if rowcount > 0:
        await _insert_download_event(
            db,
            job_id=int(job_id),
            event_type="failed",
            error_code=normalized_error_code,
        )
    await db.commit()
    return rowcount


async def retry_download_job(db: aiosqlite.Connection, job_id: int) -> dict[str, Any]:
    safe_job_id = int(job_id)
    cursor = await db.execute(
        """
        SELECT id, status
        FROM download_jobs
        WHERE id = ?
        """,
        (safe_job_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {"updated": 0, "reason": "not_found"}

    status = str(row["status"] or "").strip().lower()
    if status != DOWNLOAD_STATUS_FAILED:
        return {"updated": 0, "reason": "invalid_status"}

    updated = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'pending',
            attempt_count = attempt_count + 1,
            error_code = NULL,
            error_message = NULL,
            requested_at = datetime('now'),
            started_at = NULL,
            finished_at = NULL,
            updated_at = datetime('now')
        WHERE id = ?
          AND status = 'failed'
        """,
        (safe_job_id,),
    )
    rowcount = int(updated.rowcount or 0)
    if rowcount <= 0:
        return {"updated": 0, "reason": "already_changed"}

    await _insert_download_event(
        db,
        job_id=safe_job_id,
        event_type="retried",
    )
    await db.commit()
    return {"updated": rowcount, "reason": "ok"}


async def recover_stuck_download_jobs(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        SELECT id
        FROM download_jobs
        WHERE status = 'running'
        ORDER BY id ASC
        """
    )
    rows = await cursor.fetchall()
    job_ids = [int(row["id"]) for row in rows]
    if not job_ids:
        return 0

    updated = await db.execute(
        """
        UPDATE download_jobs
        SET
            status = 'failed',
            error_code = 'worker_interrupted',
            error_message = 'download worker interrupted (app restart/shutdown)',
            finished_at = datetime('now'),
            updated_at = datetime('now')
        WHERE status = 'running'
        """
    )
    for job_id in job_ids:
        await _insert_download_event(
            db,
            job_id=job_id,
            event_type="failed",
            error_code="worker_interrupted",
        )
    await db.commit()
    return int(updated.rowcount or 0)


async def count_download_jobs_by_status(db: aiosqlite.Connection) -> dict[str, int]:
    cursor = await db.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
        FROM download_jobs
        """
    )
    row = await cursor.fetchone()
    return {
        DOWNLOAD_STATUS_PENDING: int((row["pending_count"] if row else 0) or 0),
        DOWNLOAD_STATUS_RUNNING: int((row["running_count"] if row else 0) or 0),
        DOWNLOAD_STATUS_SUCCEEDED: int((row["succeeded_count"] if row else 0) or 0),
        DOWNLOAD_STATUS_FAILED: int((row["failed_count"] if row else 0) or 0),
    }


async def list_download_jobs(
    db: aiosqlite.Connection,
    *,
    status: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> list[dict[str, Any]]:
    normalized_status = normalize_download_status_filter(status)
    safe_limit = max(1, min(200, int(limit)))
    safe_page = max(1, int(page))
    offset = (safe_page - 1) * safe_limit
    if normalized_status == "all":
        cursor = await db.execute(
            """
            SELECT
                id,
                video_id,
                video_title,
                status,
                quality,
                overwrite,
                target_dir,
                attempt_count,
                output_path,
                file_size_bytes,
                error_code,
                error_message,
                requested_at,
                started_at,
                finished_at,
                updated_at
            FROM download_jobs
            ORDER BY requested_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (safe_limit, offset),
        )
    else:
        cursor = await db.execute(
            """
            SELECT
                id,
                video_id,
                video_title,
                status,
                quality,
                overwrite,
                target_dir,
                attempt_count,
                output_path,
                file_size_bytes,
                error_code,
                error_message,
                requested_at,
                started_at,
                finished_at,
                updated_at
            FROM download_jobs
            WHERE status = ?
            ORDER BY requested_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (
                normalized_status,
                safe_limit,
                offset,
            ),
        )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def count_download_jobs(
    db: aiosqlite.Connection,
    *,
    status: str | None = None,
) -> int:
    normalized_status = normalize_download_status_filter(status)
    if normalized_status == "all":
        cursor = await db.execute("SELECT COUNT(1) AS cnt FROM download_jobs")
    else:
        cursor = await db.execute(
            "SELECT COUNT(1) AS cnt FROM download_jobs WHERE status = ?",
            (normalized_status,),
        )
    row = await cursor.fetchone()
    return int((row["cnt"] if row else 0) or 0)


async def latest_download_event_id(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT MAX(id) AS max_id FROM download_events")
    row = await cursor.fetchone()
    return int((row["max_id"] if row else 0) or 0)


async def list_download_events_after(
    db: aiosqlite.Connection,
    *,
    after_event_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    safe_after = max(0, int(after_event_id))
    safe_limit = max(1, min(200, int(limit)))
    cursor = await db.execute(
        """
        SELECT
            e.id,
            e.job_id,
            e.event_type,
            e.error_code,
            e.created_at,
            j.video_id,
            j.video_title,
            j.status
        FROM download_events e
        JOIN download_jobs j ON j.id = e.job_id
        WHERE e.id > ?
        ORDER BY e.id ASC
        LIMIT ?
        """,
        (safe_after, safe_limit),
    )
    rows = await cursor.fetchall()
    return _rows_to_dicts(rows)


async def get_download_progress(
    db: aiosqlite.Connection,
    *,
    after_event_id: int,
    event_limit: int = 100,
) -> dict[str, Any]:
    counts = await count_download_jobs_by_status(db)
    events = await list_download_events_after(
        db,
        after_event_id=after_event_id,
        limit=event_limit,
    )
    latest_event = await latest_download_event_id(db)
    return {
        "counts": counts,
        "events": events,
        "latest_event_id": latest_event,
    }


def is_newer_published(candidate: str, watermark: str | None) -> bool:
    if watermark is None:
        return True

    try:
        candidate_dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        watermark_dt = datetime.fromisoformat(watermark.replace("Z", "+00:00"))
    except ValueError:
        return candidate > watermark
    return candidate_dt > watermark_dt
