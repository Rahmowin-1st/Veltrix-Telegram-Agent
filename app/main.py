from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from app.ai.agent import Agent
from app.ai.gemini_client import GeminiClient
from app.api import router
from app.config import Settings, get_settings
from app.db import Database
from app.logging_config import configure_logging
from app.telegram.handlers import TelegramRuntime
from app.telegram.mtproto_client import MTProtoClient
from app.tools.confirm import ConfirmationManager
from app.tools.telegram_read import TelegramReadTools
from app.tools.telegram_write import TelegramWriteTools


@dataclass
class Runtime:
    settings: Settings
    db: Database
    gemini: GeminiClient
    mtproto: MTProtoClient
    confirmations: ConfirmationManager
    reads: TelegramReadTools
    writes: TelegramWriteTools
    agent: Agent
    telegram: TelegramRuntime


settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger(__name__)


def build_runtime() -> Runtime:
    db = Database(settings)
    gemini = GeminiClient(settings)
    mtproto = MTProtoClient(settings)
    confirmations = ConfirmationManager()
    reads = TelegramReadTools(mtproto)
    writes = TelegramWriteTools(mtproto, settings, confirmations)
    agent = Agent(settings, db, gemini, reads, writes)
    telegram = TelegramRuntime(settings, db, gemini, agent, confirmations, writes)
    agent.bot_reactor = telegram.react_to_message
    return Runtime(settings, db, gemini, mtproto, confirmations, reads, writes, agent, telegram)


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = build_runtime()
    app.state.runtime = runtime
    await runtime.db.init()
    try:
        await runtime.mtproto.start()
    except Exception:
        log.exception("MTProto startup failed; continuing in reduced mode")
    try:
        await runtime.telegram.start()
    except Exception:
        log.exception("Bot startup failed; health server will remain available")
    yield
    await runtime.telegram.stop()
    await runtime.mtproto.stop()
    await runtime.gemini.close()
    await runtime.db.engine.dispose()


app = FastAPI(title="Veltrix Telegram Agent", version="1.0.0", lifespan=lifespan)
app.include_router(router)
