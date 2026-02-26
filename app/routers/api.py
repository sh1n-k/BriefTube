from __future__ import annotations

from urllib.parse import parse_qs
from starlette.datastructures import UploadFile

from fastapi import APIRouter, HTTPException, Query, Request

from app import repository
from app.i18n import SUPPORTED_LANGUAGES, normalize_language
from app.timezone_policy import SUPPORTED_TIMEZONES, normalize_timezone
from app.services.bulk_channels import (
    collect_inputs_from_sources,
    parse_takeout_entries,
    resolve_bulk_inputs,
)

router = APIRouter(prefix="/api", tags=["api"])


def _parse_bool_input(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@router.get("/channels")
async def get_channels(request: Request):
    return await repository.list_channels(request.app.state.runtime.db)


@router.post("/channels")
async def create_channel(request: Request):
    channel_id = ""
    channel_name = ""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        channel_id = str(payload.get("channel_id", "")).strip()
        channel_name = str(payload.get("channel_name", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        channel_id = str((parsed.get("channel_id") or [""])[0]).strip()
        channel_name = str((parsed.get("channel_name") or [""])[0]).strip()

    if not channel_id or not channel_name:
        raise HTTPException(status_code=400, detail="channel_id and channel_name are required")

    return await repository.add_channel(
        request.app.state.runtime.db,
        channel_id=channel_id,
        channel_name=channel_name,
    )


@router.post("/channels/bulk/resolve")
async def resolve_bulk_channels(request: Request):
    bulk_text = ""
    takeout_data = parse_takeout_entries("takeout.txt", b"")
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await request.json()
        bulk_text = str(payload.get("bulk_text", ""))
        raw_entries = [str(item).strip() for item in payload.get("takeout_entries", []) if str(item).strip()]
        if raw_entries:
            takeout_data = parse_takeout_entries("takeout.txt", "\n".join(raw_entries).encode("utf-8"))
        else:
            takeout_data = parse_takeout_entries("takeout.txt", b"")
    else:
        form = await request.form()
        bulk_text = str(form.get("bulk_text", ""))
        upload = form.get("takeout_file")
        if isinstance(upload, UploadFile):
            file_content = await upload.read()
            takeout_data = parse_takeout_entries(
                filename=upload.filename or "takeout.txt",
                content=file_content,
            )

    collected = collect_inputs_from_sources(
        bulk_text=bulk_text,
        takeout_data=takeout_data,
    )
    if not collected["inputs"] and not collected["direct_channels"]:
        return {
            "ok": True,
            "total_inputs": 0,
            "resolved": [],
            "needs_selection": [],
            "failed": [],
        }

    return await resolve_bulk_inputs(
        inputs=collected["inputs"],
        direct_channels=collected["direct_channels"],
        resolver=request.app.state.runtime.channel_resolver,
    )


def _normalize_commit_items(raw_items: list[dict]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    normalized: list[tuple[str, str]] = []
    for item in raw_items:
        channel_id = str(item.get("channel_id", "")).strip()
        channel_name = str(item.get("channel_name", "")).strip()
        if not channel_id or not channel_name:
            continue
        if channel_id in seen:
            continue
        seen.add(channel_id)
        normalized.append((channel_id, channel_name))
    return normalized


@router.post("/channels/bulk/commit")
async def commit_bulk_channels(request: Request):
    items: list[dict] = []
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        items = payload.get("items", [])
    else:
        form = await request.form()
        resolved_ids = list(form.getlist("resolved_channel_id"))
        resolved_names = list(form.getlist("resolved_channel_name"))
        for channel_id, channel_name in zip(resolved_ids, resolved_names):
            items.append({"channel_id": channel_id, "channel_name": channel_name})

        for key in form.keys():
            if not key.startswith("candidate_select_"):
                continue
            packed = str(form.get(key, ""))
            if "|||" not in packed:
                continue
            channel_id, channel_name = packed.split("|||", 1)
            items.append({"channel_id": channel_id, "channel_name": channel_name})

    normalized = _normalize_commit_items(items)
    if not normalized:
        raise HTTPException(status_code=400, detail="no valid channels to save")

    saved = 0
    for channel_id, channel_name in normalized:
        await repository.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        saved += 1
    return {"ok": True, "saved": saved}


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str, request: Request):
    result = await repository.delete_channels_with_related_data(
        request.app.state.runtime.db,
        [channel_id],
    )
    request.app.state.runtime.rss_cache.pop(channel_id, None)
    if result["deleted_channels"] == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {
        "ok": True,
        "channel_id": channel_id,
        "deleted_channels": result["deleted_channels"],
        "deleted_videos": result["deleted_videos"],
    }


@router.get("/videos")
async def get_videos(
    request: Request,
    channel_id: str | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    if limit is None:
        limit = await repository.get_videos_per_page_setting(request.app.state.runtime.db)

    return await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
    )


@router.get("/videos/{video_id}")
async def get_video(video_id: str, request: Request):
    detail = await repository.get_video_detail(request.app.state.runtime.db, video_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Video not found")
    return detail


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str, request: Request):
    transcript = await repository.get_transcript(request.app.state.runtime.db, video_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


@router.get("/videos/{video_id}/article")
async def get_article(video_id: str, request: Request):
    article = await repository.get_article(request.app.state.runtime.db, video_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@router.get("/search")
async def search(request: Request, q: str = Query(min_length=1)):
    return await repository.search_documents(request.app.state.runtime.db, query=q)


@router.post("/poll/trigger")
async def trigger_poll(request: Request):
    if not await repository.is_worker_enabled(request.app.state.runtime.db, "rss"):
        return {"ok": True, "triggered": False, "reason": "rss_worker_disabled"}
    request.app.state.runtime.poll_now_event.set()
    return {"ok": True, "triggered": True}


@router.get("/status")
async def status(request: Request):
    return await repository.queue_status(request.app.state.runtime.db)


@router.post("/videos/{video_id}/retry")
async def retry_video(video_id: str, request: Request):
    affected = await repository.mark_video_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Retry target not found")
    return {"ok": True, "video_id": video_id}


@router.post("/videos/{video_id}/transcript/retry")
async def retry_transcript(video_id: str, request: Request):
    affected = await repository.reset_transcript_for_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Transcript retry target not found")
    return {"ok": True, "video_id": video_id}


@router.get("/settings")
async def get_settings(request: Request):
    language = await repository.get_setting(
        request.app.state.runtime.db,
        key="language",
        default="ko",
    )
    workers = await repository.get_worker_settings(request.app.state.runtime.db)
    policy = await repository.get_policy_settings(request.app.state.runtime.db)
    videos_per_page = await repository.get_videos_per_page_setting(request.app.state.runtime.db)
    transcript_guard = await repository.get_transcript_guard_state(request.app.state.runtime.db)
    timezone_value = await repository.get_setting(
        request.app.state.runtime.db,
        key="timezone",
        default="Asia/Seoul",
    )
    return {
        "language": normalize_language(language),
        "timezone": normalize_timezone(timezone_value),
        "workers": workers,
        "policy": policy,
        "videos_per_page": videos_per_page,
        "transcript_guard": transcript_guard,
    }


@router.post("/settings/transcript-guard/reset")
async def reset_transcript_guard(request: Request):
    guard = await repository.reset_transcript_guard_state(request.app.state.runtime.db)
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

    await repository.set_setting(request.app.state.runtime.db, key="language", value=value)
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

    await repository.set_setting(request.app.state.runtime.db, key="timezone", value=value)
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
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="videos_per_page must be integer")

    saved = await repository.set_videos_per_page_setting(request.app.state.runtime.db, value)
    return {"ok": True, "videos_per_page": saved}


@router.put("/settings/workers")
async def set_workers(request: Request):
    defaults = repository.WORKER_SETTING_DEFAULTS
    values = await repository.get_worker_settings(request.app.state.runtime.db)
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await request.json()
        workers_payload = payload.get("workers", {})
        for worker in defaults:
            if worker not in workers_payload:
                continue
            values[worker] = _parse_bool_input(
                workers_payload.get(worker),
                default=values.get(worker, defaults[worker]),
            )
    else:
        form = await request.form()
        for worker in defaults:
            # HTML checkbox: checked => "on", unchecked => missing.
            values[worker] = _parse_bool_input(
                form.get(worker),
                default=False,
            )

    saved = await repository.set_worker_settings(request.app.state.runtime.db, values)
    return {"ok": True, "workers": saved}


@router.put("/settings/policy")
async def set_policy(request: Request):
    content_type = request.headers.get("content-type", "")
    lookback_value: int | None = None
    retention_value: int | None = None
    feed_mode_value: str | None = None

    try:
        if "application/json" in content_type:
            payload = await request.json()
            if "rss_bootstrap_lookback_days" in payload:
                lookback_value = int(payload.get("rss_bootstrap_lookback_days"))
            if "retention_days" in payload:
                retention_value = int(payload.get("retention_days"))
            if "rss_feed_mode" in payload:
                feed_mode_value = str(payload["rss_feed_mode"])
        else:
            form = await request.form()
            lookback_raw = str(form.get("rss_bootstrap_lookback_days", "")).strip()
            retention_raw = str(form.get("retention_days", "")).strip()
            if lookback_raw:
                lookback_value = int(lookback_raw)
            if retention_raw:
                retention_value = int(retention_raw)
            feed_mode_raw = str(form.get("rss_feed_mode", "")).strip()
            if feed_mode_raw:
                feed_mode_value = feed_mode_raw
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="policy values must be integers")

    saved = await repository.set_policy_settings(
        request.app.state.runtime.db,
        rss_bootstrap_lookback_days=lookback_value,
        retention_days=retention_value,
        rss_feed_mode=feed_mode_value,
    )
    return {"ok": True, "policy": saved}
