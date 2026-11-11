"""Derived video pipeline vocabulary.

``video_pipeline_v`` (app/database_migrations.py) computes these per video from facts
(transcripts/articles rows), the target stage and the newest video_jobs row per kind.
"""

from __future__ import annotations

# Result-level status. *_ready: the artifact exists. *_pending: a job is queued/running.
# *_failed / no_subtitle: the current job failed. paused: stopped short of the target.
PIPELINE_STATUSES: tuple[str, ...] = (
    "paused",
    "transcript_pending",
    "transcript_failed",
    "no_subtitle",
    "transcript_ready",
    "article_pending",
    "article_failed",
    "article_ready",
)

# Work happening right now, independent of the status (an article_ready video can be
# regenerating). retry_wait: queued again after a failed attempt.
PIPELINE_ACTIVITIES: tuple[str, ...] = ("idle", "queued", "retry_wait", "running")

VIDEO_LIST_FILTER_STATUSES: tuple[str, ...] = PIPELINE_STATUSES

# Old ?pipeline_status= filter values kept working for bookmarked URLs.
LEGACY_PIPELINE_STATUS_FILTER_ALIASES: dict[str, str] = {
    "auto_paused": "paused",
    "transcript_pending": "transcript_pending",
    "transcript_processing": "transcript_pending",
    "transcript_done": "transcript_ready",
    "transcript_failed": "transcript_failed",
    "no_subtitle": "no_subtitle",
    "llm_pending": "article_pending",
    "llm_processing": "article_pending",
    "llm_failed": "article_pending",
    "manual_review": "article_failed",
    "done": "article_ready",
}

MANUAL_TRANSCRIPT_ALLOWED_PIPELINE_STATUSES: set[str] = {
    "paused",
    "transcript_failed",
    "no_subtitle",
}
MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES: set[str] = {
    "transcript_pending",
    "article_pending",
    "article_ready",
}
