from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from app.repositories import alerts_retention as alerts_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.routers.template_context import build_template_context
from app.services.thumbnail_files import cleanup_thumbnail_files

router = APIRouter(tags=["pages"])
RETENTION_PAGE_SIZE = 100
RETENTION_DELETE_BATCH_SIZE = 500


def _normalize_page_number(value: object) -> int:
    try:
        return max(1, int(str(value or "1")))
    except (TypeError, ValueError):
        return 1


async def _build_retention_page_context(
    request: Request,
    *,
    page: int,
    deleted_count: int = 0,
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
    )


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
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    targets = await alerts_repo.list_retention_expired_matching_video_ids(
        request.app.state.runtime.db,
        retention_days=int(policy["retention_days"]),
        video_ids=selected,
    )

    deleted_count = 0
    for start in range(0, len(targets), RETENTION_DELETE_BATCH_SIZE):
        result = await videos_repo.delete_videos_by_ids(
            request.app.state.runtime.db,
            targets[start : start + RETENTION_DELETE_BATCH_SIZE],
        )
        deleted_count += int(result.get("deleted", 0) or 0)
        cleanup_thumbnail_files(
            result["thumbnail_paths"],
            request.app.state.runtime.config.thumbnail_dir,
        )

    if deleted_count > 0:
        request.app.state.runtime.invalidate_retention_notice_cache()

    context = await _build_retention_page_context(
        request,
        page=_normalize_page_number(request.query_params.get("page")),
        deleted_count=deleted_count,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="retention.html",
        context=context,
    )


@router.post("/retention/delete-all")
async def delete_retention_all(request: Request):
    form = await request.form()
    confirmed = str(form.get("confirm_delete_all", "")).strip().lower()
    if confirmed != "on":
        return RedirectResponse(url="/retention", status_code=303)
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    deleted_count = 0
    while True:
        expired_ids = await alerts_repo.list_retention_expired_video_ids(
            request.app.state.runtime.db,
            retention_days=int(policy["retention_days"]),
            limit=RETENTION_DELETE_BATCH_SIZE,
        )
        if not expired_ids:
            break
        result = await videos_repo.delete_videos_by_ids(request.app.state.runtime.db, expired_ids)
        batch_deleted = int(result.get("deleted", 0) or 0)
        if batch_deleted <= 0:
            break
        deleted_count += batch_deleted
        cleanup_thumbnail_files(
            result["thumbnail_paths"],
            request.app.state.runtime.config.thumbnail_dir,
        )

    if deleted_count > 0:
        request.app.state.runtime.invalidate_retention_notice_cache()

    context = await _build_retention_page_context(
        request,
        page=1,
        deleted_count=deleted_count,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="retention.html",
        context=context,
    )
