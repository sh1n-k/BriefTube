CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    llm_enabled INTEGER NOT NULL DEFAULT 1,
    processing_stage TEXT NOT NULL DEFAULT 'off',
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_categories_sort ON categories(sort_order ASC, id ASC);

CREATE TABLE IF NOT EXISTS channels (
    channel_id              TEXT PRIMARY KEY,
    channel_name            TEXT NOT NULL,
    rss_url                 TEXT NOT NULL,
    is_active               INTEGER NOT NULL DEFAULT 1,
    last_seen_published_at  TEXT,
    rss_consecutive_404_count INTEGER NOT NULL DEFAULT 0,
    rss_404_first_at        TEXT,
    category_id             INTEGER REFERENCES categories(id),
    channel_handle          TEXT,
    channel_url_canonical   TEXT,
    channel_thumbnail_url   TEXT,
    channel_description     TEXT,
    channel_language_hint   TEXT,
    metadata_fetched_at     TEXT,
    metadata_fetch_status   TEXT NOT NULL DEFAULT 'never',
    metadata_fetch_error    TEXT,
    metadata_retry_count    INTEGER NOT NULL DEFAULT 0,
    metadata_next_fetch_at  TEXT,
    metadata_last_http_status INTEGER,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS system_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type      TEXT NOT NULL,
    channel_id      TEXT,
    channel_name    TEXT,
    message         TEXT NOT NULL,
    acknowledged_at TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO app_settings(key, value) VALUES ('language', 'ko');

CREATE TABLE IF NOT EXISTS videos (
    video_id            TEXT PRIMARY KEY,
    channel_id          TEXT NOT NULL REFERENCES channels(channel_id),
    title               TEXT NOT NULL,
    upload_time         TEXT NOT NULL,
    thumbnail_path      TEXT,
    pipeline_status     TEXT NOT NULL DEFAULT 'transcript_pending',
    processing_stage_snapshot TEXT NOT NULL DEFAULT 'full',
    transcript_retry_count INTEGER NOT NULL DEFAULT 0,
    transcript_next_attempt_at TEXT,
    transcript_target_language TEXT,
    transcript_last_error TEXT,
    transcript_last_error_at TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    viewed_at           TEXT
);

CREATE TABLE IF NOT EXISTS transcripts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT NOT NULL UNIQUE REFERENCES videos(video_id),
    raw_text    TEXT NOT NULL,
    language    TEXT,
    source_type TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT NOT NULL UNIQUE REFERENCES videos(video_id),
    title       TEXT NOT NULL,
    lead        TEXT NOT NULL,
    body        TEXT NOT NULL,
    fact_box    TEXT,
    timestamps  TEXT,
    llm_provider TEXT NOT NULL DEFAULT 'unknown',
    llm_model   TEXT NOT NULL DEFAULT '',
    llm_reasoning_effort TEXT NOT NULL DEFAULT '',
    llm_generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    raw_text,
    content=transcripts,
    content_rowid=id
);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    lead,
    body,
    content=articles,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS transcripts_ai AFTER INSERT ON transcripts BEGIN
    INSERT INTO transcripts_fts(rowid, raw_text) VALUES (new.id, new.raw_text);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_ad AFTER DELETE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, raw_text) VALUES ('delete', old.id, old.raw_text);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_au AFTER UPDATE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, raw_text) VALUES ('delete', old.id, old.raw_text);
    INSERT INTO transcripts_fts(rowid, raw_text) VALUES (new.id, new.raw_text);
END;

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, lead, body) VALUES (new.id, new.title, new.lead, new.body);
END;

CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, lead, body) VALUES ('delete', old.id, old.title, old.lead, old.body);
END;

CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, lead, body) VALUES ('delete', old.id, old.title, old.lead, old.body);
    INSERT INTO articles_fts(rowid, title, lead, body) VALUES (new.id, new.title, new.lead, new.body);
END;

CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_upload ON videos(upload_time DESC);
CREATE INDEX IF NOT EXISTS idx_system_alerts_unacked ON system_alerts(acknowledged_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_alerts_channel_type_created
    ON system_alerts(channel_id, alert_type, created_at DESC);

CREATE TABLE IF NOT EXISTS download_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id         TEXT NOT NULL,
    video_title      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    quality          TEXT NOT NULL DEFAULT '1080',
    overwrite        INTEGER NOT NULL DEFAULT 0,
    target_dir       TEXT,
    attempt_count    INTEGER NOT NULL DEFAULT 1,
    output_path      TEXT,
    file_size_bytes  INTEGER,
    error_code       TEXT,
    error_message    TEXT,
    requested_at     TEXT NOT NULL DEFAULT (datetime('now')),
    started_at       TEXT,
    finished_at      TEXT,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS download_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       INTEGER NOT NULL REFERENCES download_jobs(id) ON DELETE CASCADE,
    event_type   TEXT NOT NULL,
    error_code   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_download_jobs_status_requested
    ON download_jobs(status, requested_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_download_jobs_finished
    ON download_jobs(finished_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_download_events_job
    ON download_events(job_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_download_events_created
    ON download_events(created_at DESC, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_download_jobs_active_video
    ON download_jobs(video_id)
    WHERE status IN ('pending', 'running');

CREATE TABLE IF NOT EXISTS manual_article_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    requested_at    TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    finished_at     TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_manual_article_jobs_status_requested
    ON manual_article_jobs(status, requested_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_manual_article_jobs_video_requested
    ON manual_article_jobs(video_id, requested_at DESC, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_article_jobs_active_video
    ON manual_article_jobs(video_id)
    WHERE status IN ('pending', 'running');
