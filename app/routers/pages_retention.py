from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.repositories import alerts_retention as alerts_repo
from app.repositories import settings as settings_repo
from app.repositories import videos as videos_repo
from app.routers.template_context import build_template_context
from app.services.thumbnail_files import cleanup_thumbnail_files

router = APIRouter(tags=["pages"])


async def _render_retention_page(request: Request, *, deleted_count: int):
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    expired_videos = await alerts_repo.list_retention_expired_videos(
        request.app.state.runtime.db,
        retention_days=int(policy["retention_days"]),
    )
    context = await build_template_context(
        request,
        expired_videos=expired_videos,
        retention_days=int(policy["retention_days"]),
        deleted_count=deleted_count,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="retention.html",
        context=context,
    )


async def _delete_retention_targets(request: Request, video_ids: list[str]) -> int:
    result = await videos_repo.delete_videos_by_ids(request.app.state.runtime.db, video_ids)
    deleted_count = int(result.get("deleted", 0) or 0)
    if deleted_count > 0:
        request.app.state.runtime.invalidate_retention_notice_cache()
    cleanup_thumbnail_files(
        result["thumbnail_paths"],
        request.app.state.runtime.config.thumbnail_dir,
    )
    return deleted_count


@router.get("/retention")
async def retention_page(request: Request):
    deleted_raw = request.query_params.get("deleted", "0")
    try:
        deleted_count = max(0, int(deleted_raw))
    except (TypeError, ValueError):
        deleted_count = 0
    return await _render_retention_page(request, deleted_count=deleted_count)


@router.post("/retention/delete-selected")
async def delete_retention_selected(request: Request):
    form = await request.form()
    selected = [str(value).strip() for value in form.getlist("video_id") if str(value).strip()]
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    expired_ids = set(
        await alerts_repo.list_retention_expired_video_ids(
            request.app.state.runtime.db,
            retention_days=int(policy["retention_days"]),
        )
    )
    targets = [video_id for video_id in selected if video_id in expired_ids]
    deleted_count = await _delete_retention_targets(request, targets)
    return await _render_retention_page(request, deleted_count=deleted_count)


@router.post("/retention/delete-all")
async def delete_retention_all(request: Request):
    form = await request.form()
    confirmed = str(form.get("confirm_delete_all", "")).strip().lower()
    if confirmed != "on":
        return RedirectResponse(url="/retention", status_code=303)
    policy = await settings_repo.get_policy_settings(request.app.state.runtime.db)
    expired_ids = await alerts_repo.list_retention_expired_video_ids(
        request.app.state.runtime.db,
        retention_days=int(policy["retention_days"]),
    )
    deleted_count = await _delete_retention_targets(request, expired_ids)
    return await _render_retention_page(request, deleted_count=deleted_count)
