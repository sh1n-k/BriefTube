from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "sql" / "schema.sql"


def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(db_path: str) -> None:
    conn = get_db_connection(db_path)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    # Ensure default category exists
    row = conn.execute("SELECT id FROM categories WHERE is_default = 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO categories (name, sort_order, llm_enabled, processing_stage, is_default) "
            "VALUES ('미분류', 0, 1, 'off', 1)"
        )
    conn.commit()
    conn.close()


def seed_categories(db_path: str) -> dict[str, int]:
    conn = get_db_connection(db_path)
    row = conn.execute("SELECT id FROM categories WHERE is_default = 1").fetchone()
    default_id = row["id"] if row else 1

    # Insert additional categories
    conn.execute(
        "INSERT OR IGNORE INTO categories (name, sort_order, llm_enabled, processing_stage, is_default) "
        "VALUES ('투자', 1, 1, 'full', 0)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO categories (name, sort_order, llm_enabled, processing_stage, is_default) "
        "VALUES ('기술', 2, 1, 'transcript_only', 0)"
    )
    conn.commit()

    result: dict[str, int] = {"미분류": default_id}
    for row in conn.execute("SELECT id, name FROM categories"):
        result[row["name"]] = row["id"]
    conn.close()
    return result


def seed_channel(
    db_path: str,
    channel_id: str,
    channel_name: str,
    *,
    is_active: int = 1,
    category_id: int | None = None,
) -> None:
    conn = get_db_connection(db_path)
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    conn.execute(
        "INSERT OR IGNORE INTO channels (channel_id, channel_name, rss_url, is_active, category_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (channel_id, channel_name, rss_url, is_active, category_id),
    )
    conn.commit()
    conn.close()


def seed_video(
    db_path: str,
    video_id: str,
    channel_id: str,
    title: str,
    upload_time: str | None = None,
    *,
    pipeline_status: str = "transcript_pending",
    thumbnail_path: str | None = None,
    processing_stage_snapshot: str = "full",
) -> None:
    if upload_time is None:
        upload_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_db_connection(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO videos "
        "(video_id, channel_id, title, upload_time, pipeline_status, thumbnail_path, processing_stage_snapshot) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (video_id, channel_id, title, upload_time, pipeline_status, thumbnail_path, processing_stage_snapshot),
    )
    conn.commit()
    conn.close()


def seed_transcript(db_path: str, video_id: str, raw_text: str, *, language: str = "ko") -> None:
    conn = get_db_connection(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO transcripts (video_id, raw_text, language, source_type) "
        "VALUES (?, ?, ?, 'manual')",
        (video_id, raw_text, language),
    )
    conn.commit()
    conn.close()


def seed_article(
    db_path: str,
    video_id: str,
    title: str,
    lead: str,
    body: str,
    *,
    fact_box: str | None = None,
    timestamps: str | None = None,
) -> None:
    conn = get_db_connection(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO articles (video_id, title, lead, body, fact_box, timestamps) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (video_id, title, lead, body, fact_box, timestamps),
    )
    conn.commit()
    conn.close()


def seed_download_job(
    db_path: str,
    video_id: str,
    video_title: str,
    *,
    status: str = "pending",
    quality: str = "1080",
    output_path: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    file_size_bytes: int | None = None,
    target_dir: str | None = None,
) -> int:
    conn = get_db_connection(db_path)
    cursor = conn.execute(
        "INSERT INTO download_jobs "
        "(video_id, video_title, status, quality, output_path, error_code, error_message, "
        "file_size_bytes, target_dir) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (video_id, video_title, status, quality, output_path, error_code, error_message,
         file_size_bytes, target_dir),
    )
    job_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return job_id


def seed_app_setting(db_path: str, key: str, value: str) -> None:
    conn = get_db_connection(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (key, value),
    )
    conn.commit()
    conn.close()


def seed_system_alert(
    db_path: str,
    alert_type: str,
    message: str,
    *,
    channel_id: str | None = None,
    channel_name: str | None = None,
) -> int:
    conn = get_db_connection(db_path)
    cursor = conn.execute(
        "INSERT INTO system_alerts (alert_type, message, channel_id, channel_name) "
        "VALUES (?, ?, ?, ?)",
        (alert_type, message, channel_id, channel_name),
    )
    alert_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return alert_id


def disable_all_workers(db_path: str) -> None:
    worker_keys = [
        "worker_rss_enabled",
        "worker_transcript_enabled",
        "worker_llm_enabled",
        "worker_notifier_enabled",
    ]
    conn = get_db_connection(db_path)
    for key in worker_keys:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
            "VALUES (?, 'false', datetime('now'))",
            (key,),
        )
    conn.commit()
    conn.close()


def make_past_time(days_ago: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
