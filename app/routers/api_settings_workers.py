from __future__ import annotations

from fastapi import APIRouter, Request

from app.repositories import settings as settings_repo
from app.routers.helpers import parse_bool_input

router = APIRouter(tags=["api"])


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
            values[worker] = parse_bool_input(
                workers_payload.get(worker),
                default=values.get(worker, defaults[worker]),
            )
    else:
        form = await request.form()
        for worker in defaults:
            # HTML checkbox: checked => "on", unchecked => missing.
            values[worker] = parse_bool_input(
                form.get(worker),
                default=False,
            )

    saved = await settings_repo.set_worker_settings(request.app.state.runtime.db, values)
    return {"ok": True, "workers": saved}
