from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.repositories import settings as settings_repo
from app.routers.helpers import parse_bool_input
from app.routers.template_context import build_template_context
from app.services.telegram import build_telegram_settings_payload, configure_telegram_notifier

router = APIRouter(tags=["api"])


async def build_telegram_settings_payload_for_request(request: Request) -> dict[str, object]:
    telegram_settings = await settings_repo.get_telegram_settings(request.app.state.runtime.db)
    return build_telegram_settings_payload(
        request.app.state.runtime.config,
        stored_bot_token=telegram_settings["bot_token"],
        stored_chat_id=telegram_settings["chat_id"],
    )


async def _build_telegram_settings_fragment_context(request: Request) -> dict[str, object]:
    telegram_settings = await build_telegram_settings_payload_for_request(request)
    return await build_template_context(
        request,
        telegram_settings=telegram_settings,
    )


@router.put("/settings/telegram")
async def set_telegram_settings(request: Request):
    content_type = request.headers.get("content-type", "")
    bot_token: str | None = None
    chat_id: str | None = None
    clear_bot_token = False
    clear_chat_id = False

    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="telegram payload must be object")
        if "bot_token" in payload:
            bot_token = str(payload.get("bot_token", ""))
        if "chat_id" in payload:
            chat_id = str(payload.get("chat_id", ""))
        if "clear_bot_token" in payload:
            clear_bot_token = parse_bool_input(payload.get("clear_bot_token"), default=False)
        if "clear_chat_id" in payload:
            clear_chat_id = parse_bool_input(payload.get("clear_chat_id"), default=False)
    else:
        form = await request.form()
        if "telegram_bot_token" in form:
            bot_token = str(form.get("telegram_bot_token", ""))
        if "telegram_chat_id" in form:
            chat_id = str(form.get("telegram_chat_id", ""))
        clear_bot_token = parse_bool_input(form.get("telegram_clear_bot_token"), default=False)
        clear_chat_id = parse_bool_input(form.get("telegram_clear_chat_id"), default=False)

    if bot_token is None and chat_id is None and not clear_bot_token and not clear_chat_id:
        raise HTTPException(status_code=400, detail="empty telegram settings payload")

    if not clear_bot_token and bot_token is not None and not str(bot_token).strip():
        bot_token = None
    if not clear_chat_id and chat_id is not None and not str(chat_id).strip():
        chat_id = None

    if bot_token is None and chat_id is None and not clear_bot_token and not clear_chat_id:
        raise HTTPException(status_code=400, detail="empty telegram settings payload")

    try:
        saved = await settings_repo.set_telegram_settings(
            request.app.state.runtime.db,
            bot_token=bot_token,
            chat_id=chat_id,
            clear_bot_token=clear_bot_token,
            clear_chat_id=clear_chat_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    configure_telegram_notifier(
        request.app.state.runtime.telegram_notifier,
        request.app.state.runtime.config,
        stored_bot_token=saved["bot_token"],
        stored_chat_id=saved["chat_id"],
    )
    payload = build_telegram_settings_payload(
        request.app.state.runtime.config,
        stored_bot_token=saved["bot_token"],
        stored_chat_id=saved["chat_id"],
    )
    if request.headers.get("hx-request") == "true":
        context = await _build_telegram_settings_fragment_context(request)
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="fragments/telegram_settings_card.html",
            context=context,
        )
    return {
        "ok": True,
        "telegram_settings": payload,
    }
