from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.repositories import settings as settings_repo
from app.routers.helpers import parse_bool_input, read_json_object

router = APIRouter(tags=["api"])


@router.put("/settings/workers")
async def set_workers(request: Request):
    defaults = settings_repo.WORKER_SETTING_DEFAULTS
    values = await settings_repo.get_worker_settings(request.app.state.runtime.db)
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await read_json_object(request)
        workers_payload = payload.get("workers", {})
        if not isinstance(workers_payload, dict):
            raise HTTPException(status_code=400, detail="workers must be an object")
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
