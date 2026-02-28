from __future__ import annotations

from pathlib import Path
import logging

from fastapi import APIRouter, Form, Query, Request, Response
from starlette.datastructures import UploadFile

from app import repository
from app.i18n import DEFAULT_LANGUAGE, get_texts, normalize_language
from app.routers.template_context import build_template_context
from app.services.bulk_channels import (
    collect_inputs_from_sources,
    parse_takeout_entries,
    resolve_bulk_inputs,
)

router = APIRouter(prefix="/views", tags=["views"])
logger = logging.getLogger(__name__)


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _cleanup_thumbnail_files(thumbnail_paths: list[str], thumbnail_dir: str) -> None:
    base_dir = Path(thumbnail_dir).resolve()
    for raw_path in thumbnail_paths:
        filename = Path(raw_path).name
        if not filename:
            continue
        target = (base_dir / filename).resolve()
        if target.parent != base_dir:
            continue
        try:
            if target.exists() and target.is_file():
                target.unlink()
        except OSError:
            continue


@router.get("/channel-list")
async def channel_list(request: Request):
    channels = await repository.list_channels(request.app.state.runtime.db)
    context = await build_template_context(request, channels=channels)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context=context,
    )


def _unpack_candidate(value: str) -> tuple[str, str] | None:
    packed = value.strip()
    if "|||" not in packed:
        return None
    channel_id, channel_name = packed.split("|||", 1)
    normalized_id = channel_id.strip()
    normalized_name = channel_name.strip()
    if not normalized_id or not normalized_name:
        return None
    return normalized_id, normalized_name


async def _texts(request: Request) -> dict[str, str]:
    language = normalize_language(
        await repository.get_setting(
            request.app.state.runtime.db,
            key="language",
            default=DEFAULT_LANGUAGE,
        )
    )
    return get_texts(language)


@router.post("/channels/add")
async def add_channel(request: Request):
    form = await request.form()
    selected_candidate = str(form.get("selected_candidate", "")).strip()
    source = str(form.get("source", "")).strip()
    txt = await _texts(request)

    if selected_candidate:
        unpacked = _unpack_candidate(selected_candidate)
        if not unpacked:
            context = await build_template_context(
                request,
                add_mode="error",
                add_message=txt["channel_add_invalid_selection"],
                add_source=source,
                add_candidates=[],
            )
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="fragments/channel_add_result.html",
                context=context,
            )

        channel_id, channel_name = unpacked
        await repository.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        channels = await repository.list_channels(request.app.state.runtime.db)
        context = await build_template_context(
            request,
            add_mode="success",
            add_message=txt["channel_add_saved"],
            add_source="",
            add_candidates=[],
            channels=channels,
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/channel_add_result.html",
            context=context,
        )

    if not source:
        context = await build_template_context(
            request,
            add_mode="error",
            add_message=txt["channel_add_empty_input"],
            add_source="",
            add_candidates=[],
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/channel_add_result.html",
            context=context,
        )

    try:
        resolved = await request.app.state.runtime.channel_resolver.resolve_input(source)
    except Exception as exc:
        logger.warning(
            "event=channels.single_resolve_error source=%s error_type=%s",
            source,
            exc.__class__.__name__,
        )
        context = await build_template_context(
            request,
            add_mode="error",
            add_message=txt["channel_add_resolve_error"],
            add_source=source,
            add_candidates=[],
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/channel_add_result.html",
            context=context,
        )

    status = resolved.get("status")
    if status == "resolved":
        item = resolved.get("resolved") or {}
        channel_id = str(item.get("channel_id", "")).strip()
        channel_name = str(item.get("channel_name", "")).strip()
        if channel_id and channel_name:
            await repository.add_channel(
                request.app.state.runtime.db,
                channel_id=channel_id,
                channel_name=channel_name,
            )
            channels = await repository.list_channels(request.app.state.runtime.db)
            context = await build_template_context(
                request,
                add_mode="success",
                add_message=txt["channel_add_saved"],
                add_source="",
                add_candidates=[],
                channels=channels,
            )
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="fragments/channel_add_result.html",
                context=context,
            )

    if status == "needs_selection":
        context = await build_template_context(
            request,
            add_mode="selection",
            add_message=txt["channel_add_needs_selection"],
            add_source=source,
            add_candidates=resolved.get("candidates", []),
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/channel_add_result.html",
            context=context,
        )

    context = await build_template_context(
        request,
        add_mode="error",
        add_message=txt["channel_add_failed"],
        add_source=source,
        add_candidates=[],
        add_reason=str(resolved.get("reason", "")).strip(),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_add_result.html",
        context=context,
    )


@router.post("/channels/delete-selected")
async def delete_selected_channels(request: Request):
    form = await request.form()
    channel_ids = [str(value).strip() for value in form.getlist("channel_id") if str(value).strip()]
    result = await repository.delete_channels_with_related_data(
        request.app.state.runtime.db,
        channel_ids,
    )
    _cleanup_thumbnail_files(
        result["thumbnail_paths"],
        request.app.state.runtime.config.thumbnail_dir,
    )
    for channel_id in channel_ids:
        request.app.state.runtime.rss_cache.pop(channel_id, None)

    channels = await repository.list_channels(request.app.state.runtime.db)
    context = await build_template_context(request, channels=channels)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context=context,
    )


@router.post("/channels/{channel_id}/delete")
async def delete_single_channel(channel_id: str, request: Request):
    normalized = channel_id.strip()
    result = await repository.delete_channels_with_related_data(
        request.app.state.runtime.db,
        [normalized],
    )
    _cleanup_thumbnail_files(
        result["thumbnail_paths"],
        request.app.state.runtime.config.thumbnail_dir,
    )
    request.app.state.runtime.rss_cache.pop(normalized, None)

    channels = await repository.list_channels(request.app.state.runtime.db)
    context = await build_template_context(request, channels=channels)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/channel_list.html",
        context=context,
    )


@router.get("/video-list")
async def video_list(
    request: Request,
    channel_id: str | None = Query(default=None),
    sort: str = Query(default="upload_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    if limit is None:
        limit = await repository.get_videos_per_page_setting(request.app.state.runtime.db)

    total = await repository.count_videos(request.app.state.runtime.db, channel_id=channel_id)
    total_pages = max(1, (total + limit - 1) // limit)
    current_page = min(max(1, page), total_pages)
    videos = await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=current_page,
        limit=limit,
    )
    channels = await repository.list_channels(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        videos=videos,
        channels=channels,
        pagination={
            "page": current_page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "channel_id": channel_id or "",
            "sort": sort,
            "order": order,
        },
    )
    return request.app.state.templates.TemplateResponse(request=request, name="fragments/video_list.html", context=context)


@router.post("/videos/delete-selected")
async def delete_selected_videos(request: Request):
    form = await request.form()
    video_ids = [str(v).strip() for v in form.getlist("video_id") if str(v).strip()]

    if video_ids:
        result = await repository.delete_videos_by_ids(
            request.app.state.runtime.db, video_ids,
        )
        _cleanup_thumbnail_files(
            result["thumbnail_paths"],
            request.app.state.runtime.config.thumbnail_dir,
        )

    page = _safe_int(form.get("_page"), 1)
    limit_val = _safe_int(form.get("_limit"), 0)
    if limit_val <= 0:
        limit_val = await repository.get_videos_per_page_setting(request.app.state.runtime.db)
    sort = str(form.get("_sort") or "upload_time")
    order = str(form.get("_order") or "desc")
    channel_id = str(form.get("_channel_id") or "") or None

    total = await repository.count_videos(request.app.state.runtime.db, channel_id=channel_id)
    total_pages = max(1, (total + limit_val - 1) // limit_val)
    current_page = min(max(1, page), total_pages)
    videos = await repository.list_videos(
        request.app.state.runtime.db,
        channel_id=channel_id,
        sort=sort,
        order=order,
        page=current_page,
        limit=limit_val,
    )
    channels = await repository.list_channels(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        videos=videos,
        channels=channels,
        pagination={
            "page": current_page,
            "limit": limit_val,
            "total": total,
            "total_pages": total_pages,
            "channel_id": channel_id or "",
            "sort": sort,
            "order": order,
        },
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_list.html",
        context=context,
    )


@router.get("/video-detail/{video_id}")
async def video_detail(video_id: str, request: Request):
    detail = await repository.get_video_detail(request.app.state.runtime.db, video_id)
    context = await build_template_context(request, video=detail)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/video_detail.html",
        context=context,
    )


@router.get("/search-results")
async def search_results(request: Request, q: str = Query(default="")):
    results = await repository.search_documents(request.app.state.runtime.db, q) if q else []
    context = await build_template_context(request, results=results, q=q)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/search_results.html",
        context=context,
    )


@router.get("/status-badge/{video_id}")
async def status_badge(video_id: str, request: Request):
    video = await repository.get_video(request.app.state.runtime.db, video_id)
    status = video["pipeline_status"] if video else "unknown"
    context = await build_template_context(request, status=status)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/status_badge.html",
        context=context,
    )


@router.post("/channels/bulk-resolve")
async def bulk_resolve(request: Request):
    form = await request.form()
    bulk_text = str(form.get("bulk_text", ""))
    upload = form.get("takeout_file")
    takeout_data = parse_takeout_entries("takeout.txt", b"")
    if isinstance(upload, UploadFile):
        data = await upload.read()
        takeout_data = parse_takeout_entries(upload.filename or "takeout.txt", data)

    collected = collect_inputs_from_sources(
        bulk_text=bulk_text,
        takeout_data=takeout_data,
    )
    result = await resolve_bulk_inputs(
        inputs=collected["inputs"],
        direct_channels=collected["direct_channels"],
        resolver=request.app.state.runtime.channel_resolver,
    )
    context = await build_template_context(request, result=result)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/bulk_resolve_result.html",
        context=context,
    )


@router.post("/channels/bulk-commit")
async def bulk_commit(request: Request):
    form = await request.form()
    items: list[dict[str, str]] = []

    resolved_ids = list(form.getlist("resolved_channel_id"))
    resolved_names = list(form.getlist("resolved_channel_name"))
    for channel_id, channel_name in zip(resolved_ids, resolved_names):
        channel_id = str(channel_id).strip()
        channel_name = str(channel_name).strip()
        if channel_id and channel_name:
            items.append({"channel_id": channel_id, "channel_name": channel_name})

    for key in form.keys():
        if not key.startswith("candidate_select_"):
            continue
        packed = str(form.get(key, ""))
        if "|||" not in packed:
            continue
        channel_id, channel_name = packed.split("|||", 1)
        channel_id = channel_id.strip()
        channel_name = channel_name.strip()
        if channel_id and channel_name:
            items.append({"channel_id": channel_id, "channel_name": channel_name})

    seen: set[str] = set()
    saved = 0
    for item in items:
        channel_id = item["channel_id"]
        channel_name = item["channel_name"]
        if channel_id in seen:
            continue
        seen.add(channel_id)
        await repository.add_channel(
            request.app.state.runtime.db,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        saved += 1

    channels = await repository.list_channels(request.app.state.runtime.db)
    context = await build_template_context(
        request,
        saved=saved,
        channels=channels,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/bulk_commit_result.html",
        context=context,
    )


@router.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: int,
    request: Request,
    confirmed: str = Form(default=""),
):
    if confirmed != "on":
        return Response(status_code=400)

    affected = await repository.acknowledge_alert(
        request.app.state.runtime.db,
        alert_id=alert_id,
    )
    if affected == 0:
        return Response(status_code=404)
    return Response(status_code=200)


@router.post("/alerts/ack-group")
async def acknowledge_alert_group(
    request: Request,
    alert_type: str = Form(default=""),
    confirmed: str = Form(default=""),
):
    if confirmed != "on":
        return Response(status_code=400)

    normalized_alert_type = str(alert_type).strip()
    if not normalized_alert_type:
        return Response(status_code=400)

    affected = await repository.acknowledge_alerts_by_type(
        request.app.state.runtime.db,
        alert_type=normalized_alert_type,
    )
    if affected == 0:
        return Response(status_code=404)
    return Response(status_code=200)
