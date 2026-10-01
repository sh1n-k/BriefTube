"""Channel JSON API endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.routers.helpers import read_json_object

MAX_CHANNEL_IMPORT_BYTES = 5 * 1024 * 1024

router = APIRouter(tags=["api"])


async def _read_json_or_form_object(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return {key: form.get(key) for key in form.keys()}
    return await read_json_object(request)


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
