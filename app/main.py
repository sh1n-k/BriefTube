from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_config
from app.database import init_database, open_database, recover_stuck_jobs
from app.domains.downloads import recover_stuck_running_jobs
from app.logging_setup import configure_logging
from app.repositories import channels as channels_repo
from app.repositories import llm as llm_repo
from app.repositories import manual_articles as manual_articles_repo
from app.repositories import manual_transcripts as manual_transcripts_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.routers import api, files, pages, system, views
from app.services.channel_resolver import ChannelResolverService
from app.services.llm import UnifiedLlmClient
from app.services.llm_capabilities import LlmCapabilityProbe
from app.services.remote_sync import run_startup_pull
from app.services.rss import RSSService
from app.services.telegram import TelegramNotifier, configure_telegram_notifier
from app.services.transcript import TranscriptService
from app.services.transcript_headers import merge_with_default_headers
from app.services.yt_dlp_feed import YtDlpFeedService
from app.state import AppState
from app.worker_registry import WORKER_SPECS, WorkerSpec

logger = logging.getLogger(__name__)


def _is_background_worker_enabled(worker_name: str, *, test_allow_alias: str | None = None) -> bool:
    env_name = worker_name.upper()
    disabled = str(os.getenv(f"BRIEFTUBE_DISABLE_{env_name}_WORKER", "")).strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return False
    in_pytest = os.getenv("PYTEST_CURRENT_TEST") is not None
    allow_in_tests = (
        str(os.getenv(f"BRIEFTUBE_ENABLE_{env_name}_WORKER_IN_TESTS", "")).strip().lower()
    )
    if test_allow_alias:
        alias_value = str(os.getenv(test_allow_alias, "")).strip().lower()
        if alias_value in {"1", "true", "yes", "on"}:
            allow_in_tests = alias_value
    if in_pytest and allow_in_tests not in {"1", "true", "yes", "on"}:
        return False
    return True


def _is_worker_spec_enabled(spec: WorkerSpec) -> bool:
    return _is_background_worker_enabled(
        spec.worker_name,
        test_allow_alias=spec.test_allow_alias,
    )


def _enabled_worker_names() -> set[str]:
    return {spec.worker_name for spec in WORKER_SPECS if _is_worker_spec_enabled(spec)}


def _resolve_worker_start_specs(enabled_worker_names: set[str]) -> list[WorkerSpec]:
    append_specs = sorted(
        (
            spec
            for spec in WORKER_SPECS
            if spec.insert_at is None and spec.worker_name in enabled_worker_names
        ),
        key=lambda spec: spec.order,
    )
    insert_specs = sorted(
        (
            spec
            for spec in WORKER_SPECS
            if spec.insert_at is not None and spec.worker_name in enabled_worker_names
        ),
        key=lambda spec: spec.order,
    )
    start_specs = list(append_specs)
    for spec in insert_specs:
        start_specs.insert(int(spec.insert_at or 0), spec)
    return start_specs


def _create_worker_tasks(
    runtime: AppState,
    *,
    enabled_worker_names: set[str],
) -> list[asyncio.Task[None]]:
    return [
        asyncio.create_task(spec.factory(runtime), name=spec.task_name)
        for spec in _resolve_worker_start_specs(enabled_worker_names)
    ]


def _build_templates() -> Jinja2Templates:
    template_dir = Path(__file__).resolve().parent / "templates"
    return Jinja2Templates(directory=str(template_dir))


def _resolve_llm_response_capture_dir(env_name: str) -> str | None:
    disabled = str(os.getenv("BRIEFTUBE_LLM_RESPONSE_CAPTURE_DISABLED", "")).strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return None

    explicit = str(os.getenv("BRIEFTUBE_LLM_RESPONSE_CAPTURE_DIR", "")).strip()
    if explicit:
        return explicit

    if str(env_name).strip().lower() == "dev":
        return "./output/llm_raw"
    return None


def _should_capture_llm_response_content() -> bool:
    enabled = str(os.getenv("BRIEFTUBE_LLM_RESPONSE_CAPTURE_INCLUDE_CONTENT", "")).strip().lower()
    return enabled in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    configure_logging(config)
    try:
        Path(config.thumbnail_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.critical(
            "event=app.thumbnail_dir_unavailable path=%s error_type=%s",
            config.thumbnail_dir,
            exc.__class__.__name__,
            extra={"event": "app.thumbnail_dir_unavailable"},
        )
        raise RuntimeError(f"thumbnail_dir is not writable: {config.thumbnail_dir}") from exc
    try:
        Path(config.download_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.critical(
            "event=app.download_dir_unavailable path=%s error_type=%s",
            config.download_dir,
            exc.__class__.__name__,
            extra={"event": "app.download_dir_unavailable"},
        )
        raise RuntimeError(f"download_dir is not writable: {config.download_dir}") from exc

    db = await open_database(config.db_path)
    await init_database(db)
    await run_startup_pull(config, db)
    recovered = await recover_stuck_jobs(db)
    orphan_repaired = await llm_repo.repair_orphan_llm_candidates(db)
    recovered_download_jobs = await recover_stuck_running_jobs(db)
    recovered_manual_article_jobs = await manual_articles_repo.recover_stuck_manual_article_jobs(db)
    recovered_manual_transcript_jobs = (
        await manual_transcripts_repo.recover_stuck_manual_transcript_jobs(db)
    )
    enabled_worker_names = _enabled_worker_names()
    if not config.remote_sync_enabled:
        enabled_worker_names.discard("remote_sync")
    metadata_worker_enabled = "channel_metadata" in enabled_worker_names
    recovered_metadata_running = 0
    scheduled_metadata_targets = 0
    if metadata_worker_enabled:
        recovered_metadata_running = await channels_repo.recover_stuck_channel_metadata_running(db)
        scheduled_metadata_targets = await channels_repo.schedule_channel_metadata_backfill(db)
    logger.info(
        "event=app.recovered_stuck_jobs recovered=%s orphan_repaired=%s recovered_download_jobs=%s recovered_manual_article_jobs=%s recovered_manual_transcript_jobs=%s recovered_metadata_running=%s scheduled_metadata_targets=%s metadata_worker_enabled=%s",
        recovered,
        orphan_repaired,
        recovered_download_jobs,
        recovered_manual_article_jobs,
        recovered_manual_transcript_jobs,
        recovered_metadata_running,
        scheduled_metadata_targets,
        metadata_worker_enabled,
        extra={"event": "app.recovered_stuck_jobs"},
    )

    http_client = httpx.AsyncClient(timeout=config.http_timeout_seconds)

    runtime = AppState(
        config=config,
        db=db,
        http_client=http_client,
        rss_service=RSSService(http_client, timeout_seconds=config.rss_timeout_seconds),
        yt_dlp_service=YtDlpFeedService(
            playlist_limit=config.yt_dlp_playlist_limit,
            timeout_seconds=config.yt_dlp_timeout_seconds,
            longform_min_seconds=config.yt_dlp_longform_min_seconds,
        ),
        transcript_service=TranscriptService(
            http_client,
            request_timeout_seconds=config.transcript_fetch_timeout_seconds,
        ),
        channel_resolver=ChannelResolverService(http_client),
        llm_client=UnifiedLlmClient(
            timeout_seconds=config.llm_timeout_seconds,
            response_capture_dir=_resolve_llm_response_capture_dir(config.env),
            capture_full_response_content=_should_capture_llm_response_content(),
        ),
        llm_capability_probe=LlmCapabilityProbe(),
        telegram_notifier=TelegramNotifier(
            token="",
            chat_id="",
            client=http_client,
        ),
        started_at=datetime.now(UTC),
    )
    telegram_settings = await settings_repo.get_telegram_settings(db)
    configure_telegram_notifier(
        runtime.telegram_notifier,
        config,
        stored_bot_token=telegram_settings["bot_token"],
        stored_chat_id=telegram_settings["chat_id"],
    )
    transcript_header_overrides = await transcripts_repo.get_transcript_request_header_overrides(db)
    runtime.transcript_service.apply_transcript_request_headers(
        merge_with_default_headers(transcript_header_overrides)
    )
    llm_settings = await llm_repo.get_llm_settings(db)
    startup_runtime_plan = runtime.llm_client.resolve_runtime_plan(llm_settings)
    startup_runtime_reason = str(startup_runtime_plan.blocking_reason or "")
    if startup_runtime_reason.startswith("llm_provider_schema_invalid_"):
        alert_created = await llm_repo.ensure_llm_schema_invalid_alert(db)
        await llm_repo.set_llm_runtime_issue(
            db,
            code=startup_runtime_reason,
            message="LLM output schema is incompatible",
        )
        logger.warning(
            "event=llm.runtime_unavailable_startup reason=%s alert_created=%s",
            startup_runtime_reason,
            alert_created,
            extra={"event": "llm.runtime_unavailable_startup", "code": startup_runtime_reason},
        )
    else:
        runtime_issue = await llm_repo.get_llm_runtime_issue(db)
        runtime_issue_code = str(runtime_issue.get("code") or "").strip().lower()
        if runtime_issue_code.startswith("llm_provider_schema_invalid_"):
            await llm_repo.clear_llm_runtime_issue(db)
        await llm_repo.clear_llm_schema_invalid_alert_flag(db)

    app.state.runtime = runtime
    app.state.templates = _build_templates()
    if metadata_worker_enabled and scheduled_metadata_targets > 0:
        runtime.channel_metadata_wake_event.set()
    tasks = _create_worker_tasks(runtime, enabled_worker_names=enabled_worker_names)

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await http_client.aclose()
        await db.close()


app = FastAPI(title="BriefTube", lifespan=lifespan)
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.include_router(api.router)
app.include_router(views.router)
app.include_router(pages.router)
app.include_router(files.router)
app.include_router(system.router)
