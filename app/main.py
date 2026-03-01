from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx

from app import repository
from app.config import load_config
from app.database import init_database, open_database, recover_stuck_jobs
from app.domains.downloads import recover_stuck_running_jobs, resolve_download_file_target
from app.logging_setup import configure_logging
from app.routers import api, pages, views
from app.services.channel_resolver import ChannelResolverService
from app.services.llm import UnifiedLlmClient
from app.services.rss import RSSService
from app.services.transcript_headers import merge_with_default_headers
from app.services.telegram import TelegramNotifier
from app.services.transcript import TranscriptService
from app.state import AppState
from app.workers.llm_worker import run_llm_queue_worker
from app.workers.notifier_worker import run_telegram_notifier
from app.workers.poller import run_rss_poller
from app.workers.download_worker import run_download_worker
from app.workers.manual_article_worker import run_manual_article_worker
from app.workers.transcript_worker import run_transcript_fetcher

logger = logging.getLogger(__name__)


def _build_templates() -> Jinja2Templates:
    template_dir = Path(__file__).resolve().parent / "templates"
    return Jinja2Templates(directory=str(template_dir))


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
    orphan_repaired = await repository.repair_orphan_llm_candidates(db)
    recovered_download_jobs = await recover_stuck_running_jobs(db)
    recovered_manual_article_jobs = await repository.recover_stuck_manual_article_jobs(db)
    logger.info(
        "event=app.recovered_stuck_jobs recovered=%s orphan_repaired=%s recovered_download_jobs=%s recovered_manual_article_jobs=%s",
        recovered,
        orphan_repaired,
        recovered_download_jobs,
        recovered_manual_article_jobs,
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
        ),
        telegram_notifier=TelegramNotifier(
            token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            client=http_client,
        ),
        started_at=datetime.now(timezone.utc),
    )
    transcript_header_overrides = await repository.get_transcript_request_header_overrides(db)
    runtime.transcript_service.apply_transcript_request_headers(
        merge_with_default_headers(transcript_header_overrides)
    )

    app.state.runtime = runtime
    app.state.templates = _build_templates()

    tasks = [
        asyncio.create_task(run_rss_poller(runtime), name="rss_poller"),
        asyncio.create_task(run_download_worker(runtime), name="download_worker"),
        asyncio.create_task(run_manual_article_worker(runtime), name="manual_article_worker"),
        asyncio.create_task(run_transcript_fetcher(runtime), name="transcript_fetcher"),
        asyncio.create_task(run_llm_queue_worker(runtime), name="llm_queue_worker"),
        asyncio.create_task(run_telegram_notifier(runtime), name="telegram_notifier"),
    ]

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
