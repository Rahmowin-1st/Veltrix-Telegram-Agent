from __future__ import annotations

from fastapi import APIRouter, Request

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
        "bot_configured": state.settings.has_bot,
        "bot_running": state.telegram.running,
        "mtproto_configured": state.settings.has_mtproto,
        "mtproto_running": state.mtproto.ready,
    }


@router.get("/setup-status")
async def setup_status(request: Request):
    state = request.app.state.runtime
    status = state.settings.setup_status()
    status.update({
        "owner_bound": bool(state.settings.owner_telegram_id),
        "bot_running": state.telegram.running,
        "mtproto_running": state.mtproto.ready,
    })
    return status
