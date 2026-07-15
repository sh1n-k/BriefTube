from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.repositories import settings as settings_repo
from app.routers.helpers import read_json_object

router = APIRouter(tags=["api"])


@router.put("/settings/policy")
async def set_policy(request: Request):
    content_type = request.headers.get("content-type", "")
    lookback_value: int | None = None
    retention_value: int | None = None
    feed_mode_value: str | None = None

    try:
        if "application/json" in content_type:
            payload = await read_json_object(request)
            if "rss_bootstrap_lookback_days" in payload:
                lookback_value = int(payload["rss_bootstrap_lookback_days"])
            if "retention_days" in payload:
                retention_value = int(payload["retention_days"])
            if "rss_feed_mode" in payload:
                feed_mode_value = str(payload["rss_feed_mode"])
        else:
            form = await request.form()
            lookback_raw = str(form.get("rss_bootstrap_lookback_days", "")).strip()
            retention_raw = str(form.get("retention_days", "")).strip()
            if lookback_raw:
                lookback_value = int(lookback_raw)
            if retention_raw:
                retention_value = int(retention_raw)
            feed_mode_raw = str(form.get("rss_feed_mode", "")).strip()
            if feed_mode_raw:
                feed_mode_value = feed_mode_raw
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="policy values must be integers") from exc

    saved = await settings_repo.set_policy_settings(
        request.app.state.runtime.db,
        rss_bootstrap_lookback_days=lookback_value,
        retention_days=retention_value,
        rss_feed_mode=feed_mode_value,
    )
    if retention_value is not None:
        request.app.state.runtime.invalidate_retention_notice_cache()
    return {"ok": True, "policy": saved}
