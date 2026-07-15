"""Category JSON API endpoints."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

from app.repositories import categories as categories_repo
from app.routers.helpers import read_json_object

logger = logging.getLogger("app.routers.api")

router = APIRouter(tags=["api"])


@router.get("/categories")
async def get_categories(request: Request):
    return await categories_repo.list_categories(request.app.state.runtime.db)


@router.post("/categories")
async def create_category(request: Request):
    content_type = request.headers.get("content-type", "")
    name = ""
    if "application/json" in content_type:
        payload = await read_json_object(request)
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return category


@router.put("/categories/reorder")
async def reorder_categories(request: Request):
    content_type = request.headers.get("content-type", "")
    ordered_ids: list[int] = []
    try:
        if "application/json" in content_type:
            payload = await read_json_object(request)
            raw_ids = payload.get("ordered_ids", [])
            if not isinstance(raw_ids, list):
                raise HTTPException(status_code=400, detail="ordered_ids must be a list")
            ordered_ids = [int(i) for i in raw_ids]
        else:
            body = (await request.body()).decode("utf-8")
            parsed = parse_qs(body)
            ordered_ids = [int(i) for i in parsed.get("ordered_ids", [])]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="ordered_ids must be integers") from exc
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
        payload = await read_json_object(request)
        if "name" in payload:
            name = str(payload.get("name", "")).strip()
        if "processing_stage" in payload:
            try:
                processing_stage = categories_repo.parse_category_processing_stage(
                    payload.get("processing_stage")
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        if "name" in parsed:
            name = str(parsed["name"][0]).strip()
        if "processing_stage" in parsed:
            try:
                processing_stage = categories_repo.parse_category_processing_stage(
                    parsed["processing_stage"][0]
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    result: dict[str, object] = {"ok": True}
    try:
        if name is not None:
            rows = await categories_repo.rename_category(
                request.app.state.runtime.db, category_id, name
            )
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, request: Request):
    try:
        result = await categories_repo.delete_category(request.app.state.runtime.db, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/categories/{category_id}/channels")
async def move_channels_to_category(category_id: int, request: Request):
    content_type = request.headers.get("content-type", "")
    channel_ids: list[str] = []
    if "application/json" in content_type:
        payload = await read_json_object(request)
        raw_channel_ids = payload.get("channel_ids", [])
        if not isinstance(raw_channel_ids, list):
            raise HTTPException(status_code=400, detail="channel_ids must be a list")
        channel_ids = [str(c).strip() for c in raw_channel_ids if str(c).strip()]
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        channel_ids = [str(c).strip() for c in parsed.get("channel_ids", []) if str(c).strip()]
    if not channel_ids:
        raise HTTPException(status_code=400, detail="channel_ids is required")
    try:
        moved = await categories_repo.move_channels_to_category(
            request.app.state.runtime.db,
            channel_ids,
            category_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "moved": moved}
