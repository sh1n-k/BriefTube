from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse, Response

from app.database import database_transaction
from app.repositories import alerts_retention as alerts_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.routers.template_context import build_template_context
from app.services.thumbnail_files import cleanup_thumbnail_files

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])
RETENTION_PAGE_SIZE = 100
RETENTION_DELETE_BATCH_SIZE = 500


def _normalize_page_number(value: object) -> int:
    try:
        return max(1, int(str(value or "1")))
    except (TypeError, ValueError):
        return 1


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _retention_redirect_url(*, page: int, deleted_count: int, delete_error: bool) -> str:
    params: dict[str, str] = {}
    if deleted_count > 0:
        params["deleted"] = str(deleted_count)
    if delete_error:
        params["delete_error"] = "1"
    if page > 1:
        params["page"] = str(page)
    query = urlencode(params)
    if not query:
        return "/retention"
    return f"/retention?{query}"


async def _build_retention_page_context(
    request: Request,
    *,
    page: int,
    deleted_count: int = 0,
    delete_error: bool = False,
):
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    retention_days = int(policy["retention_days"])
    expired_total = await alerts_repo.count_retention_expired_videos(
        request.app.state.runtime.db,
        retention_days=retention_days,
    )
    page_count = max(1, (expired_total + RETENTION_PAGE_SIZE - 1) // RETENTION_PAGE_SIZE)
    safe_page = min(max(1, int(page)), page_count)
    expired_videos = await alerts_repo.list_retention_expired_videos(
        request.app.state.runtime.db,
        retention_days=retention_days,
        limit=RETENTION_PAGE_SIZE,
        offset=(safe_page - 1) * RETENTION_PAGE_SIZE,
    )
    return await build_template_context(
        request,
        expired_videos=expired_videos,
        expired_total=expired_total,
        retention_days=retention_days,
        retention_page=safe_page,
        retention_page_count=page_count,
        deleted_count=max(0, int(deleted_count)),
        delete_error=bool(delete_error),
    )


async def _render_retention(
    request: Request,
    *,
    page: int,
    deleted_count: int,
    delete_error: bool,
):
    if not _is_htmx(request):
        return RedirectResponse(
            url=_retention_redirect_url(
                page=page,
                deleted_count=deleted_count,
                delete_error=delete_error,
            ),
            status_code=303,
        )
    context = await _build_retention_page_context(
        request,
        page=page,
        deleted_count=deleted_count,
        delete_error=delete_error,
    )
    context["retention_notice_oob"] = True
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="fragments/retention_page.html",
        context=context,
    )


def _cleanup_deleted_thumbnails(request: Request, thumbnail_paths: list[str]) -> None:
    cleanup_thumbnail_files(
        thumbnail_paths,
        request.app.state.runtime.config.thumbnail_dir,
    )


async def _delete_selected_expired(
    request: Request,
    *,
    retention_days: int,
    video_ids: list[str],
) -> tuple[int, list[str]]:
    thumbnail_paths: list[str] = []
    deleted_count = 0
    async with database_transaction(request.app.state.runtime.config.db_path) as db:
        targets = await alerts_repo.list_retention_expired_matching_video_ids(
            db,
            retention_days=retention_days,
            video_ids=video_ids,
        )
        for start in range(0, len(targets), RETENTION_DELETE_BATCH_SIZE):
            result = await videos_repo.delete_videos_by_ids(
                db,
                targets[start : start + RETENTION_DELETE_BATCH_SIZE],
                commit=False,
            )
            deleted_count += int(result.get("deleted", 0) or 0)
            thumbnail_paths.extend(result["thumbnail_paths"])
    return deleted_count, thumbnail_paths


async def _delete_all_expired(
    request: Request,
    *,
    retention_days: int,
) -> tuple[int, list[str]]:
    thumbnail_paths: list[str] = []
    deleted_count = 0
    async with database_transaction(request.app.state.runtime.config.db_path) as db:
        while True:
            expired_ids = await alerts_repo.list_retention_expired_video_ids(
                db,
                retention_days=retention_days,
                limit=RETENTION_DELETE_BATCH_SIZE,
            )
            if not expired_ids:
                break
            result = await videos_repo.delete_videos_by_ids(db, expired_ids, commit=False)
            batch_deleted = int(result.get("deleted", 0) or 0)
            if batch_deleted <= 0:
                break
            deleted_count += batch_deleted
            thumbnail_paths.extend(result["thumbnail_paths"])
    return deleted_count, thumbnail_paths


@router.get("/retention")
async def retention_page(request: Request, page: int = Query(1, ge=1)):
    deleted_raw = request.query_params.get("deleted", "0")
    try:
        deleted_count = max(0, int(deleted_raw))
    except (TypeError, ValueError):
        deleted_count = 0
    context = await _build_retention_page_context(
        request,
        page=page,
        deleted_count=deleted_count,
        delete_error=request.query_params.get("delete_error") == "1",
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="retention.html",
        context=context,
    )


@router.post("/retention/delete-selected")
async def delete_retention_selected(request: Request):
    form = await request.form()
    selected = [str(value).strip() for value in form.getlist("video_id") if str(value).strip()]
    page = _normalize_page_number(request.query_params.get("page"))
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    try:
        deleted_count, thumbnail_paths = await _delete_selected_expired(
            request,
            retention_days=int(policy["retention_days"]),
            video_ids=selected,
        )
    except Exception:
        logger.exception("retention delete-selected failed")
        return await _render_retention(
            request,
            page=page,
            deleted_count=0,
            delete_error=True,
        )

    _cleanup_deleted_thumbnails(request, thumbnail_paths)
    if deleted_count > 0:
        request.app.state.runtime.invalidate_retention_notice_cache()
    return await _render_retention(
        request,
        page=page,
        deleted_count=deleted_count,
        delete_error=False,
    )


@router.post("/retention/delete-all")
async def delete_retention_all(request: Request):
    form = await request.form()
    confirmed = str(form.get("confirm_delete_all", "")).strip().lower()
    if confirmed != "on":
        if _is_htmx(request):
            return Response(status_code=204)
        return RedirectResponse(url="/retention", status_code=303)

    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    try:
        deleted_count, thumbnail_paths = await _delete_all_expired(
            request,
            retention_days=int(policy["retention_days"]),
        )
    except Exception:
        logger.exception("retention delete-all failed")
        return await _render_retention(
            request,
            page=1,
            deleted_count=0,
            delete_error=True,
        )

    _cleanup_deleted_thumbnails(request, thumbnail_paths)
    if deleted_count > 0:
        request.app.state.runtime.invalidate_retention_notice_cache()
    return await _render_retention(
        request,
        page=1,
        deleted_count=deleted_count,
        delete_error=False,
    )
