from __future__ import annotations

import asyncio
import logging
import time

from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.state import AppState

logger = logging.getLogger(__name__)


async def _sleep_with_wake(state: AppState, timeout_seconds: float) -> None:
    safe_timeout = max(0.0, float(timeout_seconds))
    wake_event = getattr(state, "llm_wake_event", None)
    if not isinstance(wake_event, asyncio.Event):
        await asyncio.sleep(safe_timeout)
        return
    if wake_event.is_set():
        wake_event.clear()
        return
    try:
        await asyncio.wait_for(wake_event.wait(), timeout=safe_timeout)
    except asyncio.TimeoutError:
        return
    finally:
        if wake_event.is_set():
            wake_event.clear()


async def run_llm_queue_worker(state: AppState) -> None:
    next_missing_config_log_at = 0.0
    next_runtime_warning_log_at = 0.0
    while True:
        if not await settings_repo.is_worker_enabled(state.db, "llm"):
            await _sleep_with_wake(state, 5)
            continue

        try:
            candidate = await llm_repo.pop_llm_candidate(state.db, state.config.max_retry_count)
            if not candidate:
                await _sleep_with_wake(state, 5)
                continue

            video_id = candidate["video_id"]
            llm_settings = await settings_repo.get_llm_settings(state.db)
            runtime_plan = state.llm_client.resolve_runtime_plan(llm_settings)
            runtime_reason = runtime_plan.blocking_reason

            if runtime_plan.warnings:
                now = time.monotonic()
                if now >= next_runtime_warning_log_at:
                    logger.warning(
                        "event=llm.fallback_disabled worker=llm video_id=%s warnings=%s providers=%s",
                        video_id,
                        ",".join(runtime_plan.warnings),
                        ",".join(runtime_plan.providers_to_try),
                        extra={"event": "llm.fallback_disabled", "worker": "llm"},
                    )
                    next_runtime_warning_log_at = now + 60.0

            if runtime_reason is not None:
                alert_created = await llm_repo.ensure_llm_config_missing_alert(state.db)
                await llm_repo.set_llm_runtime_issue(
                    state.db,
                    code=runtime_reason,
                    message="LLM runtime is not ready",
                )
                now = time.monotonic()
                if now >= next_missing_config_log_at:
                    logger.warning(
                        "event=llm.runtime_unavailable worker=llm video_id=%s reason=%s alert_created=%s",
                        video_id,
                        runtime_reason,
                        alert_created,
                        extra={"event": "llm.runtime_unavailable", "worker": "llm", "code": runtime_reason},
                    )
                    next_missing_config_log_at = now + 60.0
                await _sleep_with_wake(state, 10)
                continue

            await llm_repo.clear_llm_config_missing_alert_flag(state.db)
            marked = await llm_repo.mark_restructure_processing(state.db, video_id)
            if marked == 0:
                await _sleep_with_wake(state, 1)
                continue

            try:
                article = await state.llm_client.restructure(
                    source_title=candidate["title"],
                    transcript_text=candidate["raw_text"],
                    settings=llm_settings,
                )
                await llm_repo.save_article(
                    state.db,
                    video_id=video_id,
                    title=article["title"],
                    lead=article["lead"],
                    body=article["body"],
                    fact_box=article.get("fact_box"),
                    timestamps=article.get("timestamps"),
                )
                await state.notification_queue.put(
                    {
                        "video_id": video_id,
                        "title": article["title"],
                        "lead": article["lead"],
                    }
                )
                await llm_repo.clear_llm_runtime_issue(state.db)
                logger.info(
                    "event=llm.restructure_succeeded worker=llm video_id=%s",
                    video_id,
                    extra={"event": "llm.restructure_succeeded", "worker": "llm"},
                )
            except Exception as exc:
                error_code = str(getattr(exc, "code", "unknown") or "unknown")
                if error_code == "llm_provider_auth_required" or error_code.startswith("llm_provider_unavailable_"):
                    requeued = await llm_repo.requeue_llm_pending_without_retry(
                        state.db,
                        video_id=video_id,
                    )
                    alert_created = await llm_repo.ensure_llm_config_missing_alert(state.db)
                    await llm_repo.set_llm_runtime_issue(
                        state.db,
                        code=error_code,
                        message=str(exc),
                    )
                    logger.warning(
                        "event=llm.runtime_unavailable worker=llm video_id=%s reason=%s requeued=%s alert_created=%s",
                        video_id,
                        error_code,
                        requeued,
                        alert_created,
                        extra={"event": "llm.runtime_unavailable", "worker": "llm", "code": error_code},
                    )
                    await _sleep_with_wake(state, 5)
                    continue

                next_status, affected = await llm_repo.mark_restructure_failed(
                    state.db,
                    video_id=video_id,
                    retry_count=int(candidate["retry_count"]),
                    max_retry_count=state.config.max_retry_count,
                )
                if affected == 0:
                    logger.warning(
                        "event=llm.restructure_failed_stale_skip worker=llm video_id=%s",
                        video_id,
                        extra={"event": "llm.restructure_failed_stale_skip", "worker": "llm"},
                    )
                else:
                    error_code = getattr(exc, "code", "unknown")
                    provider = getattr(exc, "provider", None)
                    logger.exception(
                        "event=llm.restructure_failed worker=llm video_id=%s next_status=%s error_type=%s error_code=%s provider=%s",
                        video_id,
                        next_status,
                        exc.__class__.__name__,
                        error_code,
                        provider,
                        extra={"event": "llm.restructure_failed", "worker": "llm", "code": error_code},
                    )
        except Exception:
            logger.exception(
                "event=llm.worker_loop_failed worker=llm",
                extra={"event": "llm.worker_loop_failed", "worker": "llm"},
            )
            await _sleep_with_wake(state, 5)
