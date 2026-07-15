from __future__ import annotations

PIPELINE_STATUSES: tuple[str, ...] = (
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

TRANSCRIPT_QUEUE_STATUSES: tuple[str, ...] = (
    "transcript_pending",
    "transcript_processing",
    "transcript_failed",
    "no_subtitle",
)
LLM_QUEUE_STATUSES: tuple[str, ...] = (
    "llm_pending",
    "llm_processing",
    "llm_failed",
    "manual_review",
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
MANUAL_TRANSCRIPT_ALLOWED_PIPELINE_STATUSES: set[str] = {
    "auto_paused",
    "transcript_failed",
    "no_subtitle",
}
MANUAL_ARTICLE_ENQUEUE_RETRY_PIPELINE_STATUSES: set[str] = {
    "auto_paused",
    "transcript_done",
    "transcript_failed",
    "no_subtitle",
    "llm_failed",
    "manual_review",
}
MANUAL_ARTICLE_ENQUEUE_SKIP_PIPELINE_STATUSES: set[str] = {
    "transcript_pending",
    "transcript_processing",
    "llm_pending",
    "llm_processing",
    "done",
}
