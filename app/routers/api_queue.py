from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.i18n import DEFAULT_LANGUAGE, get_texts, normalize_language
from app.repositories import llm as llm_repo
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
    transcript_items = await transcripts_repo.list_queue_items(
        db,
        transcripts_repo.TRANSCRIPT_QUEUE_STATUSES,
    )
    llm_items = await transcripts_repo.list_queue_items(
        db,
        llm_repo.LLM_QUEUE_STATUSES,
    )
    counts = await transcripts_repo.queue_status(db)
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
        cleared_count = await transcripts_repo.clear_transcript_queue_items(db)
    elif section == "llm":
        cleared_count = await llm_repo.clear_llm_queue_items(db)
    else:
        raise HTTPException(status_code=404, detail="Queue section not found")
    return {"ok": True, "section": section, "cleared_count": cleared_count}


@router.get("/status")
async def status(request: Request):
    return await transcripts_repo.queue_status(request.app.state.runtime.db)
