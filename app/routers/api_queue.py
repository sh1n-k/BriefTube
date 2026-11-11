from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.i18n import DEFAULT_LANGUAGE, get_texts, normalize_language
from app.pipeline_status import LLM_QUEUE_STATUSES, TRANSCRIPT_QUEUE_STATUSES
from app.repositories import pipeline as pipeline_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo

router = APIRouter(tags=["api"])


@router.post("/poll/trigger")
async def trigger_poll(request: Request):
    if not await settings_repo.is_worker_enabled(request.app.state.runtime.db, "rss"):
        return {"ok": True, "triggered": False, "reason": "rss_worker_disabled"}
    request.app.state.runtime.poll_now_event.set()
    return {"ok": True, "triggered": True}


@router.get("/queue/poll")
async def queue_poll(request: Request):
    db = request.app.state.runtime.db
    transcript_items = await pipeline_repo.list_queue_items(
        db,
        TRANSCRIPT_QUEUE_STATUSES,
    )
    llm_items = await pipeline_repo.list_queue_items(
        db,
        LLM_QUEUE_STATUSES,
    )
    counts = await pipeline_repo.queue_status(db)
    workers = await settings_repo.get_worker_settings(db)
    guard = await transcripts_repo.get_transcript_guard_state(db)
    language_raw = await settings_repo.get_setting(db, key="language", default=DEFAULT_LANGUAGE)
    queue_html = request.app.state.templates.env.get_template("fragments/queue_list.html").render(
        {
            "txt": get_texts(normalize_language(language_raw)),
            "transcript_items": transcript_items,
            "llm_items": llm_items,
            "queue_counts": counts,
        }
    )
    badge_count = (
        counts.get("transcript_pending", 0)
        + counts.get("transcript_processing", 0)
        + counts.get("llm_pending", 0)
        + counts.get("llm_processing", 0)
    )
    return {
        "transcript_items": transcript_items,
        "llm_items": llm_items,
        "counts": counts,
        "queue_html": queue_html,
        "badge_count": badge_count,
        "workers": {
            "transcript": workers.get("transcript", True),
            "llm": workers.get("llm", True),
        },
        "transcript_guard": {
            "breaker_state": guard.get("breaker_state", "closed"),
            "cooldown_until": guard.get("cooldown_until"),
            "adaptive_factor": guard.get("adaptive_factor", 1.0),
        },
    }


@router.post("/queue/{section}/clear")
async def clear_queue_section(section: str, request: Request):
    db = request.app.state.runtime.db
    if section == "transcript":
        cleared_count = await pipeline_repo.cancel_queue(db, pipeline_repo.JOB_KIND_TRANSCRIPT)
    elif section == "llm":
        cleared_count = await pipeline_repo.cancel_queue(db, pipeline_repo.JOB_KIND_ARTICLE)
    else:
        raise HTTPException(status_code=404, detail="Queue section not found")
    return {"ok": True, "section": section, "cleared_count": cleared_count}


@router.post("/queue/{section}/retry-failed")
async def retry_failed_queue_section(section: str, request: Request):
    db = request.app.state.runtime.db
    if section == "transcript":
        retried_count = await pipeline_repo.retry_failed_jobs(db, pipeline_repo.JOB_KIND_TRANSCRIPT)
        if retried_count > 0:
            request.app.state.runtime.transcript_wake_event.set()
    elif section == "llm":
        retried_count = await pipeline_repo.retry_failed_jobs(db, pipeline_repo.JOB_KIND_ARTICLE)
        if retried_count > 0:
            request.app.state.runtime.llm_wake_event.set()
    else:
        raise HTTPException(status_code=404, detail="Queue section not found")
    return {"ok": True, "section": section, "retried_count": retried_count}


@router.get("/status")
async def status(request: Request):
    return await pipeline_repo.queue_status(request.app.state.runtime.db)
