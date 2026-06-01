"""Settings JSON API endpoints."""

from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

from app.i18n import SUPPORTED_LANGUAGES, normalize_language
from app.repositories import downloads as downloads_repo
from app.repositories import remote_sync as remote_sync_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.routers import (
    api_settings_llm,
    api_settings_policy,
    api_settings_telegram,
    api_settings_transcript_headers,
    api_settings_workers,
)
from app.routers.api_settings_llm import (
    resolve_llm_capabilities_payload,
    resolve_llm_runtime_status_payload,
)
from app.routers.api_settings_telegram import build_telegram_settings_payload_for_request
from app.routers.api_settings_transcript_headers import build_transcript_header_payload
from app.services.downloads import is_ffmpeg_available
from app.timezone_policy import SUPPORTED_TIMEZONES, normalize_timezone

router = APIRouter(tags=["api"])
router.include_router(api_settings_llm.router)
router.include_router(api_settings_policy.router)
router.include_router(api_settings_telegram.router)
router.include_router(api_settings_transcript_headers.router)
router.include_router(api_settings_workers.router)


@router.get("/settings")
async def get_settings(request: Request):
    language = await settings_repo.get_setting(
        request.app.state.runtime.db,
        key="language",
        default="ko",
    )
    workers = await settings_repo.get_worker_settings(request.app.state.runtime.db)
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    videos_per_page = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)
    transcript_guard = await transcripts_repo.get_transcript_guard_state(
        request.app.state.runtime.db
    )
    timezone_value = await settings_repo.get_setting(
        request.app.state.runtime.db,
        key="timezone",
        default="Asia/Seoul",
    )
    transcript_request_header_overrides = (
        await transcripts_repo.get_transcript_request_header_overrides(request.app.state.runtime.db)
    )
    transcript_request_headers = build_transcript_header_payload(
        transcript_request_header_overrides
    )
    download_defaults = await downloads_repo.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    llm_settings = await settings_repo.get_llm_settings(request.app.state.runtime.db)
    llm_runtime_status = await resolve_llm_runtime_status_payload(request)
    llm_capabilities = await resolve_llm_capabilities_payload(request)
    telegram_settings = await build_telegram_settings_payload_for_request(request)
    remote_sync_status = await remote_sync_repo.get_status(
        request.app.state.runtime.db,
        configured=bool(request.app.state.runtime.config.remote_sync_dsn),
        enabled=bool(request.app.state.runtime.config.remote_sync_enabled),
    )
    return {
        "language": normalize_language(language),
        "timezone": normalize_timezone(timezone_value),
        "workers": workers,
        "policy": policy,
        "videos_per_page": videos_per_page,
        "transcript_guard": transcript_guard,
        "transcript_request_headers": transcript_request_headers,
        "download_defaults": download_defaults,
        "llm_settings": llm_settings,
        "llm_runtime_status": llm_runtime_status,
        "llm_capabilities": llm_capabilities,
        "telegram_settings": telegram_settings,
        "remote_sync": remote_sync_status,
        "ffmpeg_available": is_ffmpeg_available(),
    }


@router.post("/settings/transcript-guard/reset")
async def reset_transcript_guard(request: Request):
    guard = await transcripts_repo.reset_transcript_guard_state(request.app.state.runtime.db)
    return {
        "ok": True,
        "transcript_guard": guard,
    }


@router.put("/settings/language")
async def set_language(request: Request):
    content_type = request.headers.get("content-type", "")
    value = ""
    if "application/json" in content_type:
        payload = await request.json()
        value = str(payload.get("language", "")).strip().lower()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        value = str((parsed.get("language") or [""])[0]).strip().lower()

    if value not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="language must be one of: ko, en")

    await settings_repo.set_setting(request.app.state.runtime.db, key="language", value=value)
    return {"ok": True, "language": value}


@router.put("/settings/timezone")
async def set_timezone(request: Request):
    content_type = request.headers.get("content-type", "")
    value = ""
    if "application/json" in content_type:
        payload = await request.json()
        value = str(payload.get("timezone", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        value = str((parsed.get("timezone") or [""])[0]).strip()

    if value not in SUPPORTED_TIMEZONES:
        raise HTTPException(status_code=400, detail="unsupported timezone")

    await settings_repo.set_setting(request.app.state.runtime.db, key="timezone", value=value)
    return {"ok": True, "timezone": value}


@router.put("/settings/videos-per-page")
async def set_videos_per_page(request: Request):
    content_type = request.headers.get("content-type", "")
    raw_value = ""
    if "application/json" in content_type:
        payload = await request.json()
        raw_value = str(payload.get("videos_per_page", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        raw_value = str((parsed.get("videos_per_page") or [""])[0]).strip()

    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="videos_per_page must be integer") from exc

    saved = await settings_repo.set_videos_per_page_setting(request.app.state.runtime.db, value)
    return {"ok": True, "videos_per_page": saved}
