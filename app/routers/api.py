from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs
from starlette.datastructures import UploadFile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from app.i18n import SUPPORTED_LANGUAGES, get_texts, normalize_language
from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.repositories import downloads as downloads_repo
from app.repositories import llm as llm_repo
from app.repositories import manual_articles as manual_articles_repo
from app.repositories import settings as settings_repo
from app.repositories import transcripts as transcripts_repo
from app.repositories import videos as videos_repo
from app.routers import api_downloads
from app.services.downloads import is_ffmpeg_available
from app.services.llm_runtime import (
    LlmRuntimeStatus,
    is_runtime_ready_for_resume,
    resolve_llm_runtime_status,
    runtime_reason_text,
    runtime_reason_text_key,
)
from app.timezone_policy import SUPPORTED_TIMEZONES, normalize_timezone
from app.services.bulk_channels import (
    collect_inputs_from_sources,
    parse_takeout_entries,
    resolve_bulk_inputs,
)
from app.services.transcript_headers import (
    TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS,
    TRANSCRIPT_REQUEST_HEADER_KEYS,
    TRANSCRIPT_REQUEST_HEADER_PROFILE,
    compact_header_overrides,
    default_transcript_request_headers,
    format_headers_multiline,
    merge_with_default_headers,
    parse_headers_from_fields,
    parse_headers_multiline,
    validate_complete_header_fields,
)

router = APIRouter(prefix="/api", tags=["api"])
router.include_router(api_downloads.router)
logger = logging.getLogger(__name__)
ARTICLE_REQUEST_BULK_LIMIT = 10


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


def _build_transcript_header_payload(overrides: dict[str, str]) -> dict[str, object]:
    compact = compact_header_overrides(overrides, strict=False)
    values = merge_with_default_headers(compact)
    defaults = default_transcript_request_headers()
    return {
        "profile": TRANSCRIPT_REQUEST_HEADER_PROFILE,
        "keys": list(TRANSCRIPT_REQUEST_HEADER_KEYS),
        "field_names": dict(TRANSCRIPT_REQUEST_HEADER_FORM_FIELDS),
        "defaults": defaults,
        "values": values,
        "multiline": format_headers_multiline(values),
    }


def _build_llm_runtime_toast_header(message: str, tone: str) -> dict[str, str]:
    payload = {
        "llm-runtime-toast": {
            "message": message,
            "tone": tone,
        }
    }
    return {"HX-Trigger": json.dumps(payload, ensure_ascii=True)}


async def _resolve_llm_runtime_status(request: Request) -> dict[str, object]:
    llm_settings = await settings_repo.get_llm_settings(request.app.state.runtime.db)
    runtime_issue = await llm_repo.get_llm_runtime_issue(request.app.state.runtime.db)
    pending_count = await llm_repo.count_llm_pending_videos(request.app.state.runtime.db)
    status = resolve_llm_runtime_status(
        llm_client=request.app.state.runtime.llm_client,
        llm_settings=llm_settings,
        runtime_issue=runtime_issue,
        pending_count=pending_count,
    )
    return {
        "ready": status.ready,
        "code": status.code,
        "reason": status.reason,
        "reason_text_key": runtime_reason_text_key(status.code),
        "providers_to_try": status.providers_to_try,
        "warnings": status.warnings,
        "pending_count": status.pending_count,
    }


@router.get("/categories")
async def get_categories(request: Request):
    return await categories_repo.list_categories(request.app.state.runtime.db)


@router.post("/categories")
async def create_category(request: Request):
    content_type = request.headers.get("content-type", "")
    name = ""
    if "application/json" in content_type:
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        name = str((parsed.get("name") or [""])[0]).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        category = await categories_repo.create_category(request.app.state.runtime.db, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return category


@router.put("/categories/reorder")
async def reorder_categories(request: Request):
    content_type = request.headers.get("content-type", "")
    ordered_ids: list[int] = []
    if "application/json" in content_type:
        payload = await request.json()
        raw_ids = payload.get("ordered_ids", [])
        ordered_ids = [int(i) for i in raw_ids]
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        ordered_ids = [int(i) for i in parsed.get("ordered_ids", [])]
    if not ordered_ids:
        raise HTTPException(status_code=400, detail="ordered_ids is required")
    updated = await categories_repo.reorder_categories(request.app.state.runtime.db, ordered_ids)
    return {"ok": True, "updated": updated}


@router.put("/categories/{category_id}")
async def update_category(category_id: int, request: Request):
    content_type = request.headers.get("content-type", "")
    name: str | None = None
    processing_stage: str | None = None
    if "application/json" in content_type:
        payload = await request.json()
        if "name" in payload:
            name = str(payload.get("name", "")).strip()
        if "processing_stage" in payload:
            try:
                processing_stage = categories_repo.parse_category_processing_stage(payload.get("processing_stage"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        if "name" in parsed:
            name = str(parsed["name"][0]).strip()
        if "processing_stage" in parsed:
            try:
                processing_stage = categories_repo.parse_category_processing_stage(parsed["processing_stage"][0])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

    result: dict[str, object] = {"ok": True}
    try:
        if name is not None:
            rows = await categories_repo.rename_category(request.app.state.runtime.db, category_id, name)
            if rows == 0:
                raise HTTPException(status_code=404, detail="category not found")
            result["renamed"] = True
        if processing_stage is not None:
            rows = await categories_repo.update_category_processing_stage(
                request.app.state.runtime.db,
                category_id,
                processing_stage,
            )
            if rows == 0:
                raise HTTPException(status_code=404, detail="category not found")
            result["processing_stage"] = processing_stage
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, request: Request):
    try:
        result = await categories_repo.delete_category(request.app.state.runtime.db, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.post("/categories/{category_id}/channels")
async def move_channels_to_category(category_id: int, request: Request):
    content_type = request.headers.get("content-type", "")
    channel_ids: list[str] = []
    if "application/json" in content_type:
        payload = await request.json()
        channel_ids = [str(c).strip() for c in payload.get("channel_ids", []) if str(c).strip()]
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        channel_ids = [str(c).strip() for c in parsed.get("channel_ids", []) if str(c).strip()]
    if not channel_ids:
        raise HTTPException(status_code=400, detail="channel_ids is required")
    try:
        moved = await categories_repo.move_channels_to_category(
            request.app.state.runtime.db, channel_ids, category_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "moved": moved}


@router.get("/channels")
async def get_channels(request: Request):
    return await channels_repo.list_channels(request.app.state.runtime.db)


@router.post("/channels")
async def create_channel(request: Request):
    channel_id = ""
    channel_name = ""
    channel_handle: str | None = None
    channel_url_canonical: str | None = None
    channel_thumbnail_url: str | None = None
    channel_description: str | None = None
    channel_language_hint: str | None = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        channel_id = str(payload.get("channel_id", "")).strip()
        channel_name = str(payload.get("channel_name", "")).strip()
        channel_handle = str(payload.get("channel_handle", "")).strip() or None
        channel_url_canonical = str(payload.get("channel_url_canonical", "")).strip() or None
        channel_thumbnail_url = str(payload.get("channel_thumbnail_url", "")).strip() or None
        channel_description = str(payload.get("channel_description", "")).strip() or None
        channel_language_hint = str(payload.get("channel_language_hint", "")).strip() or None
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        channel_id = str((parsed.get("channel_id") or [""])[0]).strip()
        channel_name = str((parsed.get("channel_name") or [""])[0]).strip()
        channel_handle = str((parsed.get("channel_handle") or [""])[0]).strip() or None
        channel_url_canonical = str((parsed.get("channel_url_canonical") or [""])[0]).strip() or None
        channel_thumbnail_url = str((parsed.get("channel_thumbnail_url") or [""])[0]).strip() or None
        channel_description = str((parsed.get("channel_description") or [""])[0]).strip() or None
        channel_language_hint = str((parsed.get("channel_language_hint") or [""])[0]).strip() or None

    if not channel_id or not channel_name:
        raise HTTPException(status_code=400, detail="channel_id and channel_name are required")

    created = await channels_repo.add_channel(
        request.app.state.runtime.db,
        channel_id=channel_id,
        channel_name=channel_name,
        channel_handle=channel_handle,
        channel_url_canonical=channel_url_canonical,
        channel_thumbnail_url=channel_thumbnail_url,
        channel_description=channel_description,
        channel_language_hint=channel_language_hint,
    )
    await channels_repo.enqueue_channel_metadata_refresh(
        request.app.state.runtime.db,
        channel_id=channel_id,
    )
    request.app.state.runtime.channel_metadata_wake_event.set()
    refreshed = await channels_repo.get_channel_by_id(
        request.app.state.runtime.db,
        channel_id,
    )
    return refreshed or created


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
        await channels_repo.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        await channels_repo.enqueue_channel_metadata_refresh(
            request.app.state.runtime.db,
            channel_id=channel_id,
        )
        saved += 1
    if saved > 0:
        request.app.state.runtime.channel_metadata_wake_event.set()
    return {"ok": True, "saved": saved}


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str, request: Request):
    result = await channels_repo.delete_channels_with_related_data(
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


@router.get("/channels/export")
async def export_channels(
    request: Request,
    status: str = Query(default="active"),
    category_id: int | None = Query(default=None),
):
    db = request.app.state.runtime.db
    channels = await channels_repo.list_channels_for_management(db, status, category_id)
    now = datetime.now(timezone.utc)
    export_data = {
        "version": 1,
        "exported_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filter": {"status": status, "category_id": category_id},
        "channels": [
            {
                "channel_id": ch["channel_id"],
                "channel_name": ch["channel_name"],
                "channel_handle": ch.get("channel_handle"),
                "channel_url_canonical": ch.get("channel_url_canonical"),
                "category_name": None if ch.get("category_is_default") else ch.get("category_name"),
            }
            for ch in channels
        ],
    }
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    filename = f"brieftube_channels_{status}_{timestamp}.json"
    json_str = json.dumps(export_data, ensure_ascii=False, indent=2)
    return Response(
        content=json_str,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/channels/import")
async def import_channels(request: Request):
    form = await request.form()
    upload = form.get("import_file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="import_file is required")

    try:
        raw = await upload.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid json file")

    if not isinstance(data, dict) or data.get("version") != 1:
        raise HTTPException(status_code=400, detail="unsupported export version")

    entries = data.get("channels", [])
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="channels must be an array")

    db = request.app.state.runtime.db
    categories = await categories_repo.list_categories(db)
    cat_name_map: dict[str, int] = {c["name"]: c["id"] for c in categories}
    default_cat_id = await categories_repo.get_default_category_id(db)
    created_categories: list[str] = []

    added = 0
    duplicate = 0
    invalid = 0

    for entry in entries:
        if not isinstance(entry, dict):
            invalid += 1
            continue
        channel_id = str(entry.get("channel_id", "")).strip()
        channel_name = str(entry.get("channel_name", "")).strip()
        if not channel_id or not channel_name:
            invalid += 1
            continue

        existing = await channels_repo.get_channel_by_id(db, channel_id)
        if existing:
            duplicate += 1
            continue

        cat_name = entry.get("category_name")
        target_cat_id = default_cat_id
        if cat_name and isinstance(cat_name, str):
            cat_name = cat_name.strip()
            if cat_name in cat_name_map:
                target_cat_id = cat_name_map[cat_name]
            else:
                try:
                    new_cat = await categories_repo.create_category(db, name=cat_name)
                    cat_name_map[cat_name] = new_cat["id"]
                    target_cat_id = new_cat["id"]
                    created_categories.append(cat_name)
                except ValueError:
                    target_cat_id = default_cat_id

        await channels_repo.add_channel(
            db,
            channel_id=channel_id,
            channel_name=channel_name,
            category_id=target_cat_id,
            channel_handle=str(entry.get("channel_handle", "")).strip() or None,
            channel_url_canonical=str(entry.get("channel_url_canonical", "")).strip() or None,
        )
        await channels_repo.enqueue_channel_metadata_refresh(db, channel_id=channel_id)
        added += 1

    if added > 0:
        request.app.state.runtime.channel_metadata_wake_event.set()

    return {
        "ok": True,
        "added": added,
        "duplicate": duplicate,
        "invalid": invalid,
        "created_categories": len(created_categories),
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
        limit = await settings_repo.get_videos_per_page_setting(request.app.state.runtime.db)

    return await videos_repo.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
    )


@router.get("/videos/{video_id}")
async def get_video(video_id: str, request: Request):
    detail = await videos_repo.get_video_detail(request.app.state.runtime.db, video_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Video not found")
    return detail


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str, request: Request):
    transcript = await videos_repo.get_transcript(request.app.state.runtime.db, video_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


@router.get("/videos/{video_id}/article")
async def get_article(video_id: str, request: Request):
    article = await videos_repo.get_article(request.app.state.runtime.db, video_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@router.get("/search")
async def search(request: Request, q: str = Query(min_length=1)):
    return await videos_repo.search_documents(request.app.state.runtime.db, query=q)


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
        db, transcripts_repo.TRANSCRIPT_QUEUE_STATUSES,
    )
    llm_items = await transcripts_repo.list_queue_items(
        db, llm_repo.LLM_QUEUE_STATUSES,
    )
    counts = await transcripts_repo.queue_status(db)
    workers = await settings_repo.get_worker_settings(db)
    guard = await transcripts_repo.get_transcript_guard_state(db)
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


@router.get("/status")
async def status(request: Request):
    return await transcripts_repo.queue_status(request.app.state.runtime.db)


@router.post("/videos/{video_id}/retry")
async def retry_video(video_id: str, request: Request):
    affected = await videos_repo.mark_video_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Retry target not found")
    return {"ok": True, "video_id": video_id}


@router.post("/videos/article-request")
async def request_videos_article(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        raise HTTPException(status_code=415, detail="application/json content-type required")

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid json payload") from exc

    raw_video_ids = payload.get("video_ids", []) if isinstance(payload, dict) else []
    if not isinstance(raw_video_ids, list):
        raise HTTPException(status_code=400, detail="video_ids must be an array")

    selected_ids = [str(video_id).strip() for video_id in raw_video_ids if str(video_id).strip()]
    video_ids = list(dict.fromkeys(selected_ids))
    if not video_ids:
        raise HTTPException(status_code=400, detail="video_ids is required")
    if len(video_ids) > ARTICLE_REQUEST_BULK_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"video_ids can include up to {ARTICLE_REQUEST_BULK_LIMIT} items",
        )

    bulk_result = await manual_articles_repo.enqueue_manual_article_jobs(
        request.app.state.runtime.db,
        video_ids=video_ids,
    )
    new_count = int(bulk_result.get("new_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    retry_count = int(bulk_result.get("retry_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    skip_count = int(bulk_result.get("skip_count", 0) or 0) if isinstance(bulk_result, dict) else 0
    failed_count = int(bulk_result.get("failed_count", 0) or 0) if isinstance(bulk_result, dict) else 0

    if (new_count + retry_count) > 0:
        request.app.state.runtime.manual_article_wake_event.set()

    llm_worker_waiting = not await settings_repo.is_worker_enabled(
        request.app.state.runtime.db,
        "llm",
    )

    return {
        "ok": True,
        "requested_count": len(video_ids),
        "summary": {
            "new": new_count,
            "retry": retry_count,
            "skip": skip_count,
            "failed": failed_count,
        },
        "llm_worker_waiting": llm_worker_waiting,
    }


@router.post("/videos/{video_id}/transcript/retry")
async def retry_transcript(video_id: str, request: Request):
    affected = await transcripts_repo.reset_transcript_for_retry(request.app.state.runtime.db, video_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="Transcript retry target not found")
    return {"ok": True, "video_id": video_id}


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
    transcript_guard = await transcripts_repo.get_transcript_guard_state(request.app.state.runtime.db)
    timezone_value = await settings_repo.get_setting(
        request.app.state.runtime.db,
        key="timezone",
        default="Asia/Seoul",
    )
    transcript_request_header_overrides = await transcripts_repo.get_transcript_request_header_overrides(
        request.app.state.runtime.db
    )
    transcript_request_headers = _build_transcript_header_payload(transcript_request_header_overrides)
    download_defaults = await downloads_repo.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    llm_settings = await settings_repo.get_llm_settings(request.app.state.runtime.db)
    llm_runtime_status = await _resolve_llm_runtime_status(request)
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
        "ffmpeg_available": is_ffmpeg_available(),
    }


@router.post("/settings/transcript-guard/reset")
async def reset_transcript_guard(request: Request):
    guard = await transcripts_repo.reset_transcript_guard_state(request.app.state.runtime.db)
    return {
        "ok": True,
        "transcript_guard": guard,
    }


@router.put("/settings/transcript-request-headers")
async def set_transcript_request_headers(request: Request):
    content_type = request.headers.get("content-type", "")
    try:
        parsed: dict[str, str] = {}
        has_field_input = False
        raw_text = ""
        if "application/json" in content_type:
            payload = await request.json() or {}
            if isinstance(payload, dict):
                parsed, has_field_input = parse_headers_from_fields(payload)
                raw_text = str(
                    payload.get("headers_text", payload.get("transcript_request_headers", "")) or ""
                )
            if has_field_input and raw_text.strip():
                raise ValueError("mixed header input modes are not allowed")
            if has_field_input:
                validate_complete_header_fields(payload if isinstance(payload, dict) else {})
            elif raw_text.strip():
                parsed = parse_headers_multiline(raw_text)
            else:
                raise ValueError("empty header payload is not allowed")
        else:
            form = await request.form()
            form_payload = {key: form.get(key) for key in form.keys()}
            parsed, has_field_input = parse_headers_from_fields(form_payload)
            raw_text = str(form.get("transcript_request_headers", "") or "")
            if has_field_input and raw_text.strip():
                raise ValueError("mixed header input modes are not allowed")
            if has_field_input:
                validate_complete_header_fields(form_payload)
            elif raw_text.strip():
                parsed = parse_headers_multiline(raw_text)
            else:
                raise ValueError("empty header payload is not allowed")

        overrides = compact_header_overrides(parsed, strict=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    saved_overrides = await transcripts_repo.save_transcript_request_header_overrides(
        request.app.state.runtime.db,
        overrides,
    )
    applied_values = merge_with_default_headers(saved_overrides)
    request.app.state.runtime.transcript_service.apply_transcript_request_headers(applied_values)
    payload = _build_transcript_header_payload(saved_overrides)
    return {
        "ok": True,
        "transcript_request_headers": payload,
    }


@router.put("/settings/llm")
async def set_llm_settings(request: Request):
    content_type = request.headers.get("content-type", "")
    provider_primary: str | None = None
    provider_fallback: str | None = None
    prompt_template: str | None = None
    llm_model: dict[str, str] | None = None
    llm_reasoning_effort: dict[str, str] | None = None

    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="llm payload must be object")
        if "provider_primary" in payload:
            provider_primary = str(payload.get("provider_primary", "")).strip().lower()
        if "provider_fallback" in payload:
            provider_fallback = str(payload.get("provider_fallback", "")).strip().lower()
        if "prompt_template" in payload:
            prompt_template = str(payload.get("prompt_template", ""))
        if "llm_model" in payload:
            llm_model_payload = payload.get("llm_model")
            if not isinstance(llm_model_payload, dict):
                raise HTTPException(status_code=400, detail="llm_model must be object")
            llm_model = {
                key: str(value or "")
                for key, value in llm_model_payload.items()
            }
        if "llm_reasoning_effort" in payload:
            llm_reasoning_effort_payload = payload.get("llm_reasoning_effort")
            if not isinstance(llm_reasoning_effort_payload, dict):
                raise HTTPException(status_code=400, detail="llm_reasoning_effort must be object")
            llm_reasoning_effort = {
                key: str(value or "")
                for key, value in llm_reasoning_effort_payload.items()
            }
    else:
        form = await request.form()
        if "llm_provider_primary" in form:
            provider_primary = str(form.get("llm_provider_primary", "")).strip().lower()
        if "llm_provider_fallback" in form:
            provider_fallback = str(form.get("llm_provider_fallback", "")).strip().lower()
        if "llm_prompt_template" in form:
            prompt_template = str(form.get("llm_prompt_template", ""))
        model_keys = ("llm_model_codex", "llm_model_claude", "llm_model_gemini")
        if any(key in form for key in model_keys):
            llm_model = {}
            if "llm_model_codex" in form:
                llm_model["codex"] = str(form.get("llm_model_codex", ""))
            if "llm_model_claude" in form:
                llm_model["claude"] = str(form.get("llm_model_claude", ""))
            if "llm_model_gemini" in form:
                llm_model["gemini"] = str(form.get("llm_model_gemini", ""))
        reasoning_keys = (
            "llm_reasoning_effort_codex",
            "llm_reasoning_effort_claude",
            "llm_reasoning_effort_gemini",
        )
        if any(key in form for key in reasoning_keys):
            llm_reasoning_effort = {}
            if "llm_reasoning_effort_codex" in form:
                llm_reasoning_effort["codex"] = str(form.get("llm_reasoning_effort_codex", ""))
            if "llm_reasoning_effort_claude" in form:
                llm_reasoning_effort["claude"] = str(form.get("llm_reasoning_effort_claude", ""))
            if "llm_reasoning_effort_gemini" in form:
                llm_reasoning_effort["gemini"] = str(form.get("llm_reasoning_effort_gemini", ""))

    if (
        provider_primary is None
        and provider_fallback is None
        and prompt_template is None
        and llm_model is None
        and llm_reasoning_effort is None
    ):
        raise HTTPException(status_code=400, detail="empty llm settings payload")

    try:
        current = await settings_repo.get_llm_settings(request.app.state.runtime.db)
        candidate = await settings_repo.set_llm_settings(
            request.app.state.runtime.db,
            provider_primary=provider_primary,
            provider_fallback=provider_fallback,
            prompt_template=prompt_template,
            llm_model=llm_model,
            llm_reasoning_effort=llm_reasoning_effort,
            persist=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    runtime_plan = request.app.state.runtime.llm_client.resolve_runtime_plan(candidate)
    runtime_reason = str(runtime_plan.blocking_reason or "").strip().lower()
    if runtime_reason.startswith("llm_provider_schema_invalid_"):
        await llm_repo.set_llm_runtime_issue(
            request.app.state.runtime.db,
            code=runtime_reason,
            message="LLM output schema is incompatible",
        )
        await llm_repo.ensure_llm_schema_invalid_alert(request.app.state.runtime.db)
        language = normalize_language(
            await settings_repo.get_setting(
                request.app.state.runtime.db,
                key="language",
                default="ko",
            )
        )
        txt = get_texts(language)
        reason_text = runtime_reason_text(runtime_reason, txt)
        message = txt["settings_llm_runtime_resume_blocked_toast"].format(reason=reason_text)
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "detail": "llm schema preflight failed",
                "code": runtime_reason,
                "llm_settings": current,
            },
            headers=_build_llm_runtime_toast_header(message, "error"),
        )

    saved = await settings_repo.set_llm_settings(
        request.app.state.runtime.db,
        provider_primary=provider_primary,
        provider_fallback=provider_fallback,
        prompt_template=prompt_template,
        llm_model=llm_model,
        llm_reasoning_effort=llm_reasoning_effort,
    )

    runtime_issue = await llm_repo.get_llm_runtime_issue(request.app.state.runtime.db)
    runtime_issue_code = str(runtime_issue.get("code") or "").strip().lower()
    if runtime_issue_code.startswith("llm_provider_schema_invalid_"):
        await llm_repo.clear_llm_runtime_issue(request.app.state.runtime.db)
    await llm_repo.clear_llm_schema_invalid_alert_flag(request.app.state.runtime.db)

    return {
        "ok": True,
        "llm_settings": saved,
    }


@router.get("/settings/llm/runtime-status")
async def get_llm_runtime_status(request: Request):
    return await _resolve_llm_runtime_status(request)


@router.post("/settings/llm/resume")
async def resume_llm_runtime(request: Request):
    language = normalize_language(
        await settings_repo.get_setting(
            request.app.state.runtime.db,
            key="language",
            default="ko",
        )
    )
    txt = get_texts(language)
    status_payload = await _resolve_llm_runtime_status(request)
    status = LlmRuntimeStatus(
        ready=bool(status_payload.get("ready")),
        code=str(status_payload.get("code") or ""),
        reason=str(status_payload.get("reason") or ""),
        providers_to_try=list(status_payload.get("providers_to_try") or []),
        warnings=list(status_payload.get("warnings") or []),
        pending_count=int(status_payload.get("pending_count") or 0),
    )
    if not is_runtime_ready_for_resume(status):
        reason_text = runtime_reason_text(str(status_payload.get("code") or ""), txt)
        message = txt["settings_llm_runtime_resume_blocked_toast"].format(reason=reason_text)
        return JSONResponse(
            status_code=409,
            content={"ok": False, "status": status_payload},
            headers=_build_llm_runtime_toast_header(message, "error"),
        )

    pending_count = int(status_payload["pending_count"])
    if pending_count > 0:
        request.app.state.runtime.llm_wake_event.set()
        message = txt["settings_llm_runtime_resume_requested_toast"].format(count=pending_count)
        tone = "success"
    else:
        message = txt["settings_llm_runtime_resume_no_pending_toast"]
        tone = "info"
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "resumed_count": pending_count,
            "status": status_payload,
        },
        headers=_build_llm_runtime_toast_header(message, tone),
    )


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
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="videos_per_page must be integer")

    saved = await settings_repo.set_videos_per_page_setting(request.app.state.runtime.db, value)
    return {"ok": True, "videos_per_page": saved}


@router.put("/settings/workers")
async def set_workers(request: Request):
    defaults = settings_repo.WORKER_SETTING_DEFAULTS
    values = await settings_repo.get_worker_settings(request.app.state.runtime.db)
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

    saved = await settings_repo.set_worker_settings(request.app.state.runtime.db, values)
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

    saved = await settings_repo.set_policy_settings(
        request.app.state.runtime.db,
        rss_bootstrap_lookback_days=lookback_value,
        retention_days=retention_value,
        rss_feed_mode=feed_mode_value,
    )
    return {"ok": True, "policy": saved}
