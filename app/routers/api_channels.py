"""Channel JSON API endpoints."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from app.domains.channels import delete_channels_and_cleanup
from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.routers.helpers import read_json_object
from app.services.bulk_channels import (
    MAX_TAKEOUT_IMPORT_BYTES,
    TakeoutImportTooLargeError,
    collect_inputs_from_sources,
    parse_takeout_entries,
    resolve_bulk_inputs,
)

logger = logging.getLogger("app.routers.api")
MAX_CHANNEL_IMPORT_BYTES = 5 * 1024 * 1024

router = APIRouter(tags=["api"])


async def _read_json_or_form_object(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return {key: form.get(key) for key in form.keys()}
    return await read_json_object(request)


def _parse_takeout_entries_or_413(filename: str, content: bytes):
    try:
        return parse_takeout_entries(filename, content)
    except TakeoutImportTooLargeError as exc:
        raise HTTPException(status_code=413, detail="takeout file is too large") from exc


@router.get("/channels")
async def get_channels(request: Request):
    return await channels_repo.list_channels(request.app.state.runtime.db)


@router.patch("/channels/{channel_id}/rss-priority")
async def update_channel_rss_priority(channel_id: str, request: Request):
    payload = await _read_json_or_form_object(request)
    raw_priority = str(payload.get("priority", "")).strip().lower()
    if raw_priority not in channels_repo.RSS_PRIORITY_OPTIONS:
        raise HTTPException(status_code=400, detail="invalid rss priority")
    updated = await channels_repo.update_rss_priority(
        request.app.state.runtime.db,
        channel_id.strip(),
        raw_priority,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return updated


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
        payload = await read_json_object(request)
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
        channel_url_canonical = (
            str((parsed.get("channel_url_canonical") or [""])[0]).strip() or None
        )
        channel_thumbnail_url = (
            str((parsed.get("channel_thumbnail_url") or [""])[0]).strip() or None
        )
        channel_description = str((parsed.get("channel_description") or [""])[0]).strip() or None
        channel_language_hint = (
            str((parsed.get("channel_language_hint") or [""])[0]).strip() or None
        )

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
    takeout_data = _parse_takeout_entries_or_413("takeout.txt", b"")
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await read_json_object(request)
        bulk_text = str(payload.get("bulk_text", ""))
        raw_takeout_entries = payload.get("takeout_entries", [])
        if raw_takeout_entries is None:
            raw_takeout_entries = []
        if not isinstance(raw_takeout_entries, list):
            raise HTTPException(status_code=400, detail="takeout_entries must be a list")
        raw_entries = [str(item).strip() for item in raw_takeout_entries if str(item).strip()]
        if raw_entries:
            raw_bytes = "\n".join(raw_entries).encode("utf-8")
            if len(raw_bytes) > MAX_TAKEOUT_IMPORT_BYTES:
                raise HTTPException(status_code=413, detail="takeout entries are too large")
            takeout_data = _parse_takeout_entries_or_413("takeout.txt", raw_bytes)
        else:
            takeout_data = _parse_takeout_entries_or_413("takeout.txt", b"")
    else:
        form = await request.form()
        bulk_text = str(form.get("bulk_text", ""))
        upload = form.get("takeout_file")
        if isinstance(upload, UploadFile):
            file_content = await upload.read()
            takeout_data = _parse_takeout_entries_or_413(
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
        if not isinstance(item, dict):
            continue
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
        payload = await read_json_object(request)
        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list):
            raise HTTPException(status_code=400, detail="items must be a list")
        items = raw_items
    else:
        form = await request.form()
        resolved_ids = list(form.getlist("resolved_channel_id"))
        resolved_names = list(form.getlist("resolved_channel_name"))
        for channel_id, channel_name in zip(resolved_ids, resolved_names, strict=False):
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
    result = await delete_channels_and_cleanup(request.app.state.runtime, [channel_id])
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
    now = datetime.now(UTC)
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
        if len(raw) > MAX_CHANNEL_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail="import file is too large")
        data = json.loads(raw)
    except HTTPException:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid json file") from exc

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
