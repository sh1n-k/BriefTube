from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.routers.helpers import parse_optional_int, request_texts
from app.routers.views_common import _render_category_sidebar

router = APIRouter(tags=["views"])


@router.get("/category-sidebar")
async def category_sidebar(
    request: Request,
    category_id: int | None = Query(default=None),
    status: str = Query(default=channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE),
):
    return await _render_category_sidebar(
        request, selected_category_id=category_id, channel_status=status
    )


@router.post("/categories")
async def create_category_fragment(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    txt = await request_texts(request)
    if not name:
        raise HTTPException(
            status_code=400, detail=txt.get("category_add_empty_error", "Name required")
        )
    try:
        await categories_repo.create_category(request.app.state.runtime.db, name)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=txt.get("category_add_duplicate_error", "Duplicate")
        ) from exc
    status = channels_repo.normalize_channel_management_status(str(form.get("status", "")).strip())
    selected = parse_optional_int(form.get("category_id"))
    return await _render_category_sidebar(
        request,
        selected_category_id=selected,
        channel_status=status,
        refresh_channel_list=True,
        channel_list_category_id=selected,
    )


@router.put("/categories/{category_id}/cycle-processing-stage")
async def cycle_category_processing_stage_fragment(category_id: int, request: Request):
    next_stage = await categories_repo.cycle_category_processing_stage(
        request.app.state.runtime.db, category_id
    )
    if next_stage is None:
        raise HTTPException(status_code=404, detail="category not found")
    status = request.query_params.get("status", channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE)
    raw_cat = request.query_params.get("category_id")
    selected = int(raw_cat) if raw_cat and str(raw_cat).strip().isdigit() else None
    return await _render_category_sidebar(
        request, selected_category_id=selected, channel_status=status
    )


@router.delete("/categories/{category_id}")
async def delete_category_fragment(category_id: int, request: Request):
    try:
        await categories_repo.delete_category(request.app.state.runtime.db, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    status = channels_repo.normalize_channel_management_status(
        request.query_params.get("status", channels_repo.CHANNEL_MANAGEMENT_STATUS_ACTIVE)
    )
    raw_cat = request.query_params.get("category_id")
    selected = int(raw_cat) if raw_cat and str(raw_cat).strip().isdigit() else None
    if selected == category_id:
        selected = None
    return await _render_category_sidebar(
        request,
        selected_category_id=selected,
        channel_status=status,
        refresh_channel_list=True,
        channel_list_category_id=selected,
    )
