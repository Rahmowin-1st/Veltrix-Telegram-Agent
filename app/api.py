from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.ai.schemas import TOOL_DECLARATIONS
from app.telegram.handlers import WebhookBusy

router = APIRouter()


@router.get("/")
async def root(request: Request):
    return {
        "name": "Veltrix Telegram Agent",
        "status": "running",
        "health": "/healthz",
        "setup": "/setup-status",
    }


@router.get("/healthz")
async def health(request: Request):
    state = request.app.state.runtime
    return {
        "ok": True,
        "gemini": state.settings.has_gemini,
        "ai_function_call_probe_passed": state.gemini.readiness_probe_passed,
        "ai_last_http_status": state.gemini.last_http_status,
        "bot_configured": state.settings.has_bot,
        "bot_running": state.telegram.running,
        "bot_mode": state.telegram.mode,
        "mtproto_configured": state.settings.has_mtproto,
        "mtproto_running": state.mtproto.ready,
        "memory_backend": state.db.backend,
        "ai_tool_count": len(TOOL_DECLARATIONS),
        "webhook_queue_depth": state.telegram.application.update_queue.qsize()
        if state.telegram.application
        else 0,
        "queued_jobs_durable": False,
    }


@router.get("/setup-status")
async def setup_status(request: Request):
    state = request.app.state.runtime
    status = state.settings.setup_status()
    status.update(
        {
            "owner_bound": bool(state.settings.owner_telegram_id),
            "bot_running": state.telegram.running,
            "bot_mode": state.telegram.mode,
            "mtproto_running": state.mtproto.ready,
            "memory_backend": state.db.backend,
        }
    )
    return status


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    state = request.app.state.runtime
    secret = request.headers.get("x-telegram-bot-api-secret-token")
    if not state.telegram.valid_webhook_secret(secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > state.settings.webhook_max_bytes:
            raise HTTPException(status_code=413, detail="Payload too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or type(payload.get("update_id")) is not int:
            raise ValueError("Invalid update")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid update") from exc
    try:
        await state.telegram.process_webhook(payload)
    except WebhookBusy as exc:
        raise HTTPException(
            status_code=503, detail="Retry later", headers={"Retry-After": "5"}
        ) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="Invalid update") from exc
    return {"ok": True}
