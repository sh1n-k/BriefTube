from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response

from app.repositories import alerts_retention as alerts_repo

router = APIRouter(tags=["views"])


@router.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: int,
    request: Request,
    confirmed: str = Form(default=""),
):
    if confirmed != "on":
        return Response(status_code=400)

    affected = await alerts_repo.acknowledge_alert(
        request.app.state.runtime.db,
        alert_id=alert_id,
    )
    if affected == 0:
        return Response(status_code=404)
    request.app.state.runtime.invalidate_alert_groups_cache()
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

    affected = await alerts_repo.acknowledge_alerts_by_type(
        request.app.state.runtime.db,
        alert_type=normalized_alert_type,
    )
    if affected == 0:
        return Response(status_code=404)
    request.app.state.runtime.invalidate_alert_groups_cache()
    return Response(status_code=200)
