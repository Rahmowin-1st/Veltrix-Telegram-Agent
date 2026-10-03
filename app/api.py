from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

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
    }


@router.get("/setup-status")
async def setup_status(request: Request):
    state = request.app.state.runtime
    status = state.settings.setup_status()
    status.update({
        "owner_bound": bool(state.settings.owner_telegram_id),
        "bot_running": state.telegram.running,
        "bot_mode": state.telegram.mode,
        "mtproto_running": state.mtproto.ready,
        "memory_backend": state.db.backend,
    })
    return status


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    state = request.app.state.runtime
    secret = request.headers.get("x-telegram-bot-api-secret-token")
    if not state.telegram.valid_webhook_secret(secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    payload = await request.json()
    await state.telegram.process_webhook(payload)
    return {"ok": True}
