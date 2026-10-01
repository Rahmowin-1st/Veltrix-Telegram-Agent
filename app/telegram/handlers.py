from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, TypeHandler

from app.ai.agent import Agent
from app.ai.gemini_client import GeminiClient
from app.config import Settings
from app.db import Database
from app.telegram.business import BusinessBot
from app.tools.confirm import ConfirmationManager
from app.tools.telegram_write import TelegramWriteTools

log = logging.getLogger(__name__)

HELP_TEXT = """Veltrix Telegram Agent

/start - start
/status - setup + connection status
/tools - capabilities
/memory [on|off|status] - per-chat AI memory
/forget - clear this chat's AI memory
/confirm TOKEN - confirm protected action
/cancel - cancel pending protected actions

Write normally for AI + live web research. Owner-only MTProto account tools activate only for OWNER_TELEGRAM_ID.
""".strip()

TOOLS_TEXT = """Main tools:
• live public web research + URL reading
• image/file/voice understanding
• recent Telegram chats/messages + search (owner + MTProto)
• contacts list/add/import
• send/edit/forward/pin/read/archive/mute
• protected profile/contact/block/delete actions
• per-chat wallpaper via MTProto when Telegram schema supports it
• Telegram Business connected-chat replies
""".strip()


class TelegramRuntime:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        gemini: GeminiClient,
        agent: Agent,
        confirmations: ConfirmationManager,
        writes: TelegramWriteTools,
    ):
        self.settings = settings
        self.db = db
        self.gemini = gemini
        self.agent = agent
        self.confirmations = confirmations
        self.writes = writes
        self.application: Application | None = None

    @property
    def running(self) -> bool:
        return bool(self.application and self.application.running)

    async def start(self) -> None:
        if not self.settings.telegram_bot_token:
            log.info("Bot API disabled: TELEGRAM_BOT_TOKEN missing")
            return
        self.application = Application.builder().token(self.settings.telegram_bot_token).build()
        self.application.add_handler(TypeHandler(Update, self.handle_update))
        await self.application.initialize()
        await self.application.start()
        if not self.application.updater:
            raise RuntimeError("Telegram updater unavailable")
        await self.application.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
        log.info("Telegram Bot API polling started")

    async def stop(self) -> None:
        if not self.application:
            return
        if self.application.updater:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
        self.application = None

    def _is_owner(self, user_id: int | None) -> bool:
        return bool(user_id and self.settings.owner_telegram_id and user_id == self.settings.owner_telegram_id)

    async def _reply(self, update: Update, text: str, business_connection_id: str | None = None) -> None:
        msg = update.effective_message
        if not msg:
            return
        if self.application and business_connection_id:
            await BusinessBot(self.application.bot).send_text(
                msg.chat_id, text[:4000], business_connection_id, reply_to=msg.message_id
            )
        else:
            await msg.reply_text(text[:4000])

    async def handle_update(self, update: Update, _context) -> None:
        try:
            if getattr(update, "business_connection", None):
                c = update.business_connection
                log.info("Business connection update id=%s", getattr(c, "id", None))
                return

            msg = update.effective_message
            if not msg:
                return
            business_connection_id = getattr(msg, "business_connection_id", None)
            is_business = bool(business_connection_id)
            user_id = update.effective_user.id if update.effective_user else None
            is_owner = self._is_owner(user_id)
            text = (msg.text or msg.caption or "").strip()

            if text.startswith("/") and not is_business:
                await self._handle_command(update, text, is_owner)
                return
            if is_business and not self.settings.auto_reply_business:
                return

            media_note = ""
            if msg.voice or msg.audio or msg.photo or msg.document or msg.video:
                media_note = await self._understand_media(msg) if self.gemini.ready else "[Media attached]"
            prompt = text
            if media_note:
                prompt = (text + "\n\nAttached media understanding:\n" + media_note).strip()
            if not prompt:
                prompt = "Respond appropriately to the attached media."

            # Owner account tools never leak into arbitrary bot/business chats.
            allow_account_tools = is_owner and not is_business
            response = await self.agent.chat(chat_id=msg.chat_id, text=prompt, allow_account_tools=allow_account_tools)
            await self._reply(update, response, business_connection_id)
        except Exception as exc:
            log.exception("Update handling failed")
            try:
                await self._reply(update, f"Xatolik: {type(exc).__name__}: {exc}")
            except Exception:
                pass

    async def _handle_command(self, update: Update, text: str, is_owner: bool) -> None:
        msg = update.effective_message
        assert msg is not None
        parts = text.split()
        command = parts[0].split("@", 1)[0].lower()

        if command in {"/start", "/help"}:
            await msg.reply_text(HELP_TEXT)
            return
        if command == "/tools":
            await msg.reply_text(TOOLS_TEXT)
            return
        if command == "/status":
            status = self.settings.setup_status()
            status["bot_polling"] = self.running
            status["owner_bound"] = bool(self.settings.owner_telegram_id)
            await msg.reply_text("\n".join(f"{k}: {v}" for k, v in status.items()))
            return
        if command == "/forget":
            count = await self.db.clear_history(msg.chat_id)
            await msg.reply_text(f"Memory cleared: {count} items.")
            return
        if command == "/memory":
            arg = parts[1].lower() if len(parts) > 1 else "status"
            if arg == "on":
                await self.db.set_memory_enabled(msg.chat_id, True)
                await msg.reply_text("Memory: ON")
            elif arg == "off":
                await self.db.set_memory_enabled(msg.chat_id, False)
                await msg.reply_text("Memory: OFF")
            else:
                enabled = await self.db.memory_enabled(msg.chat_id)
                await msg.reply_text(f"Memory: {'ON' if enabled else 'OFF'}")
            return
        if command == "/cancel":
            count = self.confirmations.cancel_chat(msg.chat_id)
            await msg.reply_text(f"Cancelled: {count} pending action(s).")
            return
        if command == "/confirm":
            if not is_owner:
                await msg.reply_text("Only the configured owner can confirm account actions.")
                return
            if len(parts) < 2:
                await msg.reply_text("Usage: /confirm TOKEN")
                return
            pending = self.confirmations.consume(msg.chat_id, parts[1])
            if not pending:
                await msg.reply_text("Confirmation token invalid or expired.")
                return
            result = await self.writes.execute_confirmed(pending.action, pending.arguments)
            await msg.reply_text(f"Confirmed: {pending.action}\n{result}")
            return
        await msg.reply_text("Unknown command. /help")

    async def _understand_media(self, msg) -> str:
        tg_file = None
        mime = "application/octet-stream"
        if msg.voice:
            tg_file = await msg.voice.get_file(); mime = msg.voice.mime_type or "audio/ogg"
        elif msg.audio:
            tg_file = await msg.audio.get_file(); mime = msg.audio.mime_type or "audio/mpeg"
        elif msg.video:
            tg_file = await msg.video.get_file(); mime = msg.video.mime_type or "video/mp4"
        elif msg.document:
            tg_file = await msg.document.get_file(); mime = msg.document.mime_type or mime
        elif msg.photo:
            tg_file = await msg.photo[-1].get_file(); mime = "image/jpeg"
        if not tg_file:
            return ""
        data = bytes(await tg_file.download_as_bytearray())
        if len(data) > self.settings.download_max_mb * 1024 * 1024:
            return f"Media exceeds {self.settings.download_max_mb} MB processing limit."
        return await self.gemini.understand_media(
            data=data,
            mime_type=mime,
            instruction="Understand this Telegram media. Transcribe voice/audio and summarize intent; describe images/video/documents accurately.",
        )
