from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx

from app.config import load_config
from app.database import init_database, open_database, recover_stuck_jobs
from app.domains.downloads import recover_stuck_running_jobs, resolve_download_file_target
from app.logging_setup import configure_logging
from app.repositories import channels as channels_repo
from app.repositories import llm as llm_repo
from app.repositories import manual_articles as manual_articles_repo
from app.repositories import manual_transcripts as manual_transcripts_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.routers import api, pages, views
from app.services.channel_resolver import ChannelResolverService
from app.services.llm import UnifiedLlmClient
from app.services.rss import RSSService
from app.services.transcript_headers import merge_with_default_headers
from app.services.telegram import TelegramNotifier, configure_telegram_notifier
from app.services.transcript import TranscriptService
from app.state import AppState
from app.workers.llm_worker import run_llm_queue_worker
from app.workers.notifier_worker import run_telegram_notifier
from app.workers.poller import run_rss_poller
from app.workers.channel_metadata_worker import run_channel_metadata_worker
from app.workers.download_worker import run_download_worker
from app.workers.manual_article_worker import run_manual_article_worker
from app.workers.manual_transcript_worker import run_manual_transcript_worker
from app.workers.transcript_worker import run_transcript_fetcher

logger = logging.getLogger(__name__)


def _is_channel_metadata_worker_enabled() -> bool:
    disabled = str(os.getenv("BRIEFTUBE_DISABLE_CHANNEL_METADATA_WORKER", "")).strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return False
    in_pytest = os.getenv("PYTEST_CURRENT_TEST") is not None
    allow_in_tests = str(os.getenv("BRIEFTUBE_ENABLE_METADATA_WORKER_IN_TESTS", "")).strip().lower()
    if in_pytest and allow_in_tests not in {"1", "true", "yes", "on"}:
        return False
    return True


def _is_transcript_worker_enabled() -> bool:
    disabled = str(os.getenv("BRIEFTUBE_DISABLE_TRANSCRIPT_WORKER", "")).strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return False
    in_pytest = os.getenv("PYTEST_CURRENT_TEST") is not None
    allow_in_tests = str(os.getenv("BRIEFTUBE_ENABLE_TRANSCRIPT_WORKER_IN_TESTS", "")).strip().lower()
    if in_pytest and allow_in_tests not in {"1", "true", "yes", "on"}:
        return False
    return True


def _is_manual_transcript_worker_enabled() -> bool:
    disabled = str(os.getenv("BRIEFTUBE_DISABLE_MANUAL_TRANSCRIPT_WORKER", "")).strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return False
    in_pytest = os.getenv("PYTEST_CURRENT_TEST") is not None
    allow_in_tests = str(os.getenv("BRIEFTUBE_ENABLE_MANUAL_TRANSCRIPT_WORKER_IN_TESTS", "")).strip().lower()
    if in_pytest and allow_in_tests not in {"1", "true", "yes", "on"}:
        return False
    return True


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
    recovered = await recover_stuck_jobs(db)
    orphan_repaired = await llm_repo.repair_orphan_llm_candidates(db)
    recovered_download_jobs = await recover_stuck_running_jobs(db)
    recovered_manual_article_jobs = await manual_articles_repo.recover_stuck_manual_article_jobs(db)
    recovered_manual_transcript_jobs = await manual_transcripts_repo.recover_stuck_manual_transcript_jobs(db)
    metadata_worker_enabled = _is_channel_metadata_worker_enabled()
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
        transcript_service=TranscriptService(http_client),
        channel_resolver=ChannelResolverService(http_client),
        llm_client=UnifiedLlmClient(
            timeout_seconds=config.llm_timeout_seconds,
            response_capture_dir=_resolve_llm_response_capture_dir(config.env),
            capture_full_response_content=_should_capture_llm_response_content(),
        ),
        telegram_notifier=TelegramNotifier(
            token="",
            chat_id="",
            client=http_client,
        ),
        started_at=datetime.now(timezone.utc),
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
    transcript_worker_enabled = _is_transcript_worker_enabled()
    manual_transcript_worker_enabled = _is_manual_transcript_worker_enabled()

    tasks = [
        asyncio.create_task(run_rss_poller(runtime), name="rss_poller"),
        asyncio.create_task(run_download_worker(runtime), name="download_worker"),
        asyncio.create_task(run_manual_article_worker(runtime), name="manual_article_worker"),
        asyncio.create_task(run_llm_queue_worker(runtime), name="llm_queue_worker"),
        asyncio.create_task(run_telegram_notifier(runtime), name="telegram_notifier"),
    ]
    if metadata_worker_enabled:
        tasks.insert(1, asyncio.create_task(run_channel_metadata_worker(runtime), name="channel_metadata_worker"))
    if transcript_worker_enabled:
        tasks.insert(3, asyncio.create_task(run_transcript_fetcher(runtime), name="transcript_fetcher"))
    if manual_transcript_worker_enabled:
        tasks.insert(3, asyncio.create_task(run_manual_transcript_worker(runtime), name="manual_transcript_worker"))

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


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/thumbnails/{filename:path}")
async def thumbnail(filename: str):
    safe_name = Path(filename).name
    if safe_name != filename:
        return JSONResponse(status_code=400, content={"detail": "invalid filename"})

    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        return JSONResponse(status_code=503, content={"detail": "runtime not ready"})

    target = Path(runtime.config.thumbnail_dir) / safe_name
    if not target.exists() or not target.is_file():
        return JSONResponse(status_code=404, content={"detail": "thumbnail not found"})
    return FileResponse(target)


@app.get("/downloads/files/{filename:path}")
async def download_file(
    filename: str,
    job_id: int | None = Query(default=None, ge=1),
    probe: bool = Query(default=False),
):
    safe_name = Path(filename).name
    if safe_name != filename:
        return JSONResponse(status_code=400, content={"detail": "invalid filename", "code": "invalid_filename"})

    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        return JSONResponse(status_code=503, content={"detail": "runtime not ready", "code": "runtime_not_ready"})

    target_result = await resolve_download_file_target(
        runtime.db,
        filename=safe_name,
        default_download_dir=runtime.config.download_dir,
        job_id=job_id,
    )
    if not target_result.ok:
        status_code = 400 if target_result.code == "invalid_filename" else 404
        return JSONResponse(status_code=status_code, content={"detail": target_result.message, "code": target_result.code})
    if probe:
        return {"ok": True, "filename": safe_name}
    return FileResponse(target_result.target)
