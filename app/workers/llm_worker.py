from __future__ import annotations

import asyncio
import logging
import time

from app.llm_policy import llm_min_call_interval_seconds
from app.repositories import llm as llm_repo
from app.repositories import settings as settings_repo
from app.services.llm_errors import LLM_PROVIDER_RATE_LIMITED_CODE
from app.services.llm_runtime import resolve_llm_runtime_status
from app.state import AppState, invalidate_alert_groups_cache
from app.workers.wake_sleep import sleep_with_wake_event

logger = logging.getLogger(__name__)
LLM_RETRYABLE_FAILURE_DELAY_SECONDS = 5.0
LLM_MAX_CONCURRENT_LIMIT = 4
LLM_RUNTIME_UNAVAILABLE_SLEEP_SECONDS = 5.0
LLM_IDLE_RECHECK_SECONDS = 5.0
# A subscription usage limit resets on a schedule. Requeue without spending a
# retry and pause claiming for every slot before probing again.
LLM_RATE_LIMITED_SLEEP_SECONDS = 600.0


# (backoff seconds, provider) requested by a finished job.
_JobResult = tuple[float, str]


class _ProviderBackoff:
    """Claim pause per provider, so switching provider is not held back."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}

    def extend(self, provider: str, seconds: float) -> None:
        if seconds <= 0:
            return
        until = time.monotonic() + seconds
        self._until[provider] = max(self._until.get(provider, 0.0), until)

    def until(self, provider: str) -> float:
        return self._until.get(provider, 0.0)


def _is_schema_invalid_issue(code: str) -> bool:
    return str(code or "").strip().lower().startswith("llm_provider_schema_invalid_")


def _is_runtime_unavailable_issue(code: str) -> bool:
    normalized = str(code or "").strip().lower()
    return (
        normalized == "llm_provider_auth_required"
        or normalized.startswith("llm_provider_unavailable_")
        or _is_schema_invalid_issue(normalized)
    )


def _llm_max_concurrent(llm_settings: dict) -> int:
    try:
        value = int(llm_settings.get("max_concurrent", 1))
    except (TypeError, ValueError):
        value = 1
    return max(1, min(LLM_MAX_CONCURRENT_LIMIT, value))


async def _sleep_with_wake(state: AppState, timeout_seconds: float) -> None:
    await sleep_with_wake_event(state, "llm_wake_event", timeout_seconds)


async def _wait_for_task_completion(
    active_tasks: set[asyncio.Task[_JobResult]],
) -> set[asyncio.Task[_JobResult]]:
    if not active_tasks:
        return set()
    done, _ = await asyncio.wait(
        active_tasks,
        timeout=1,
        return_when=asyncio.FIRST_COMPLETED,
    )
    return done


async def _collect_done_tasks(
    done_tasks: set[asyncio.Task[_JobResult]],
    backoff: _ProviderBackoff,
) -> None:
    for task in done_tasks:
        if task.cancelled():
            continue
        try:
            seconds, provider = task.result()
            backoff.extend(provider, seconds)
        except Exception:
            logger.exception(
                "event=llm.worker_task_failed worker=llm",
                extra={"event": "llm.worker_task_failed", "worker": "llm"},
            )


async def _clear_runtime_issue_after_success(
    state: AppState,
    *,
    runtime_issue_code_at_start: str,
) -> None:
    current_issue = await llm_repo.get_llm_runtime_issue(state.db)
    current_code = str(current_issue.get("code") if current_issue else "").strip().lower()
    started_code = str(runtime_issue_code_at_start or "").strip().lower()
    if (
        current_code
        and current_code != started_code
        and _is_runtime_unavailable_issue(current_code)
    ):
        return
    await llm_repo.clear_llm_runtime_issue(state.db)
    await llm_repo.clear_llm_config_missing_alert_flag(state.db)
    await llm_repo.clear_llm_schema_invalid_alert_flag(state.db)


async def _run_single_llm_job(
    state: AppState,
    candidate: dict,
    llm_settings: dict,
    *,
    runtime_issue_code_at_start: str,
) -> _JobResult:
    video_id = str(candidate["video_id"])
    job_provider = str(llm_settings.get("provider_primary") or "")
    try:
        article = await state.llm_client.restructure(
            source_title=candidate["title"],
            transcript_text=candidate.get("timed_text") or candidate["raw_text"],
            settings=llm_settings,
        )
        saved = await llm_repo.save_article(
            state.db,
            video_id=video_id,
            title=article["title"],
            lead=article["lead"],
            body=article["body"],
            fact_box=article.get("fact_box"),
            timestamps=article.get("timestamps"),
            llm_provider=article.get("_llm_provider"),
            llm_model=article.get("_llm_model"),
            llm_reasoning_effort=article.get("_llm_reasoning_effort"),
            llm_generated_at=article.get("_llm_generated_at"),
        )
        if saved == 0:
            logger.warning(
                "event=llm.restructure_succeeded_stale_skip worker=llm video_id=%s",
                video_id,
                extra={"event": "llm.restructure_succeeded_stale_skip", "worker": "llm"},
            )
            return (0.0, job_provider)
        await state.notification_queue.put(
            {
                "video_id": video_id,
                "title": article["title"],
                "lead": article["lead"],
            }
        )
        await _clear_runtime_issue_after_success(
            state,
            runtime_issue_code_at_start=runtime_issue_code_at_start,
        )
        logger.info(
            "event=llm.restructure_succeeded worker=llm video_id=%s",
            video_id,
            extra={"event": "llm.restructure_succeeded", "worker": "llm"},
        )
        return (0.0, job_provider)
    except Exception as exc:
        error_code = str(getattr(exc, "code", "unknown") or "unknown")
        if error_code == LLM_PROVIDER_RATE_LIMITED_CODE:
            requeued = await llm_repo.requeue_llm_pending_without_retry(
                state.db,
                video_id=video_id,
            )
            logger.warning(
                "event=llm.rate_limited worker=llm video_id=%s provider=%s requeued=%s sleep_seconds=%s",
                video_id,
                getattr(exc, "provider", None),
                requeued,
                LLM_RATE_LIMITED_SLEEP_SECONDS,
                extra={"event": "llm.rate_limited", "worker": "llm", "code": error_code},
            )
            return (LLM_RATE_LIMITED_SLEEP_SECONDS, job_provider)
        if _is_runtime_unavailable_issue(error_code):
            requeued = await llm_repo.requeue_llm_pending_without_retry(
                state.db,
                video_id=video_id,
            )
            if _is_schema_invalid_issue(error_code):
                alert_created = await llm_repo.ensure_llm_schema_invalid_alert(state.db)
            else:
                alert_created = await llm_repo.ensure_llm_config_missing_alert(state.db)
            if alert_created:
                invalidate_alert_groups_cache(state)
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
                extra={
                    "event": "llm.runtime_unavailable",
                    "worker": "llm",
                    "code": error_code,
                },
            )
            return (LLM_RUNTIME_UNAVAILABLE_SLEEP_SECONDS, job_provider)

        stderr_summary = str(getattr(exc, "stderr_summary", "") or "") or "-"
        stdout_summary = str(getattr(exc, "stdout_summary", "") or "") or "-"
        exit_code = getattr(exc, "exit_code", None)
        exit_code_label = "-" if exit_code is None else str(exit_code)

        if getattr(exc, "retryable", True) is False:
            next_status, affected = await llm_repo.mark_restructure_failed(
                state.db,
                video_id=video_id,
                retry_count=max(0, int(state.config.max_retry_count) - 1),
                max_retry_count=state.config.max_retry_count,
            )
            logger.warning(
                "event=llm.restructure_non_retryable worker=llm video_id=%s next_status=%s affected=%s error_type=%s error_code=%s provider=%s exit_code=%s stderr_summary=%s stdout_summary=%s",
                video_id,
                next_status,
                affected,
                exc.__class__.__name__,
                error_code,
                getattr(exc, "provider", None),
                exit_code_label,
                stderr_summary,
                stdout_summary,
                extra={
                    "event": "llm.restructure_non_retryable",
                    "worker": "llm",
                    "code": error_code,
                    "stderr_summary": stderr_summary,
                    "stdout_summary": stdout_summary,
                },
            )
            return (0.0, job_provider)

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
                "event=llm.restructure_failed worker=llm video_id=%s next_status=%s error_type=%s error_code=%s provider=%s exit_code=%s stderr_summary=%s stdout_summary=%s",
                video_id,
                next_status,
                exc.__class__.__name__,
                error_code,
                provider,
                exit_code_label,
                stderr_summary,
                stdout_summary,
                extra={
                    "event": "llm.restructure_failed",
                    "worker": "llm",
                    "code": error_code,
                    "stderr_summary": stderr_summary,
                    "stdout_summary": stdout_summary,
                },
            )
            if next_status == "llm_failed":
                await _sleep_with_wake(state, LLM_RETRYABLE_FAILURE_DELAY_SECONDS)
        return (0.0, job_provider)


async def run_llm_queue_worker(state: AppState) -> None:
    next_missing_config_log_at = 0.0
    next_runtime_warning_log_at = 0.0
    backoff = _ProviderBackoff()
    last_call_started_at: float | None = None
    active_tasks: set[asyncio.Task[_JobResult]] = set()
    logger.info(
        "event=llm.worker_started worker=llm",
        extra={"event": "llm.worker_started", "worker": "llm"},
    )
    while True:
        try:
            done_tasks = {task for task in active_tasks if task.done()}
            if done_tasks:
                active_tasks.difference_update(done_tasks)
                await _collect_done_tasks(done_tasks, backoff)

            if not await settings_repo.is_worker_enabled(state.db, "llm"):
                if active_tasks:
                    done_tasks = await _wait_for_task_completion(active_tasks)
                    active_tasks.difference_update(done_tasks)
                    await _collect_done_tasks(done_tasks, backoff)
                else:
                    await _sleep_with_wake(state, 5)
                continue

            llm_settings = await settings_repo.get_llm_settings(state.db)
            current_provider = str(llm_settings["provider_primary"])
            runtime_backoff_until = backoff.until(current_provider)
            max_concurrent = _llm_max_concurrent(llm_settings)
            runtime_issue = await llm_repo.get_llm_runtime_issue(state.db)
            runtime_issue_code = str(runtime_issue.get("code") if runtime_issue else "").strip()
            pending_count = await llm_repo.count_llm_pending_videos(state.db)
            runtime_status = resolve_llm_runtime_status(
                llm_client=state.llm_client,
                llm_settings=llm_settings,
                runtime_issue=runtime_issue,
                pending_count=pending_count,
            )
            runtime_reason = str(runtime_status.code or "").strip()

            if runtime_status.warnings and pending_count > 0:
                now = time.monotonic()
                if now >= next_runtime_warning_log_at:
                    logger.warning(
                        "event=llm.fallback_disabled worker=llm pending_count=%s warnings=%s providers=%s",
                        pending_count,
                        ",".join(runtime_status.warnings),
                        ",".join(runtime_status.providers_to_try),
                        extra={"event": "llm.fallback_disabled", "worker": "llm"},
                    )
                    next_runtime_warning_log_at = now + 60.0

            if runtime_reason:
                if pending_count <= 0:
                    if active_tasks:
                        done_tasks = await _wait_for_task_completion(active_tasks)
                        active_tasks.difference_update(done_tasks)
                        await _collect_done_tasks(done_tasks, backoff)
                    else:
                        await _sleep_with_wake(state, 10)
                    continue
                if _is_schema_invalid_issue(runtime_reason):
                    alert_created = await llm_repo.ensure_llm_schema_invalid_alert(state.db)
                else:
                    alert_created = await llm_repo.ensure_llm_config_missing_alert(state.db)
                if alert_created:
                    invalidate_alert_groups_cache(state)
                await llm_repo.set_llm_runtime_issue(
                    state.db,
                    code=runtime_reason,
                    message=str(runtime_status.reason or "LLM runtime is not ready"),
                )
                now = time.monotonic()
                if now >= next_missing_config_log_at:
                    logger.warning(
                        "event=llm.runtime_unavailable worker=llm pending_count=%s reason=%s alert_created=%s",
                        pending_count,
                        runtime_reason,
                        alert_created,
                        extra={
                            "event": "llm.runtime_unavailable",
                            "worker": "llm",
                            "code": runtime_reason,
                        },
                    )
                    next_missing_config_log_at = now + 60.0
                if active_tasks:
                    done_tasks = await _wait_for_task_completion(active_tasks)
                    active_tasks.difference_update(done_tasks)
                    await _collect_done_tasks(done_tasks, backoff)
                else:
                    await _sleep_with_wake(state, 10)
                continue

            if pending_count <= 0:
                if active_tasks:
                    done_tasks = await _wait_for_task_completion(active_tasks)
                    active_tasks.difference_update(done_tasks)
                    await _collect_done_tasks(done_tasks, backoff)
                else:
                    await _sleep_with_wake(state, 5)
                continue

            claimed = False
            call_interval = llm_min_call_interval_seconds(current_provider)
            next_call_at = 0.0
            while len(active_tasks) < max_concurrent and time.monotonic() >= runtime_backoff_until:
                # Claims yield to running jobs. Collect a finished job's backoff before
                # claiming again so a requeued video is not picked up straight away.
                if any(task.done() for task in active_tasks):
                    break
                if call_interval > 0 and last_call_started_at is not None:
                    next_call_at = last_call_started_at + call_interval
                    if time.monotonic() < next_call_at:
                        break
                candidate = await llm_repo.claim_next_llm_candidate(
                    state.db,
                    state.config.max_retry_count,
                )
                if not candidate:
                    break
                task = asyncio.create_task(
                    _run_single_llm_job(
                        state,
                        candidate,
                        llm_settings,
                        runtime_issue_code_at_start=runtime_issue_code,
                    )
                )
                active_tasks.add(task)
                claimed = True
                last_call_started_at = time.monotonic()
                logger.debug(
                    "event=llm.worker_claimed worker=llm video_id=%s active_tasks=%s max_concurrent=%s",
                    candidate.get("video_id"),
                    len(active_tasks),
                    max_concurrent,
                    extra={"event": "llm.worker_claimed", "worker": "llm"},
                )

            if active_tasks:
                done_tasks = await _wait_for_task_completion(active_tasks)
                active_tasks.difference_update(done_tasks)
                await _collect_done_tasks(done_tasks, backoff)
                continue
            if claimed:
                continue
            # Settings saves do not wake the worker, so re-read them every few seconds.
            resume_at = max(next_call_at, runtime_backoff_until)
            await _sleep_with_wake(
                state,
                min(LLM_IDLE_RECHECK_SECONDS, max(1.0, resume_at - time.monotonic())),
            )
        except asyncio.CancelledError:
            for task in active_tasks:
                task.cancel()
            await asyncio.gather(*active_tasks, return_exceptions=True)
            logger.info(
                "event=llm.worker_stopped worker=llm active_tasks=%s",
                len(active_tasks),
                extra={"event": "llm.worker_stopped", "worker": "llm"},
            )
            raise
        except Exception:
            logger.exception(
                "event=llm.worker_loop_failed worker=llm",
                extra={"event": "llm.worker_loop_failed", "worker": "llm"},
            )
            await _sleep_with_wake(state, 5)
