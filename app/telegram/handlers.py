from __future__ import annotations

import inspect
import json
import logging
import secrets

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
    Update,
)
from telegram.ext import Application, TypeHandler

from app.ai.agent import Agent
from app.ai.gemini_client import GeminiClient, GeminiError
from app.config import Settings
from app.db import Database
from app.security.redaction import redact_text
from app.telegram.business import BusinessBot
from app.telegram.mtproto_client import MTProtoUnavailable
from app.tools.confirm import ConfirmationManager
from app.tools.telegram_write import TelegramWriteTools

log = logging.getLogger(__name__)

HELP_TEXT = """Veltrix Telegram Agent

Men bilan o‘z tilingizda yozishing. Buyruq yoki JSON yozishingiz shart emas — kerakli amallarni o‘zim tanlayman.

Masalan:
• “Admin yuborgan oxirgi musiqani Savedga saqla va music teg qo‘y.”
• “Adminni top, unga Salom deb yubor.”
• “Shu xabarimga 👍 bos.”
• “Haftalik kreativ kontent reja tuz.”

Kim nazarda tutilgani noaniq bo‘lsa, aniqlashtiraman. Muhim o‘zgarishlarda Tasdiqlash tugmasi chiqadi.
Shaxsiy akkaunt amallaridan faqat egasi shaxsiy suhbatda foydalanadi.
""".strip()

TOOLS_TEXT = """Main tools:
• live public web research + URL reading
• image/file/voice understanding
• recent Telegram chats/messages + search (owner + MTProto)
• contacts list/add/import
• send/edit/forward/pin/read/archive/mute
• protected profile/contact/block/delete actions
• per-chat wallpaper via MTProto when Telegram schema supports it
• natural-language contact/dialog name resolution + latest incoming media
• real Premium Saved Messages tags + user/custom emoji reactions
• bot reactions on the user's current message
• join/create/manage channels subject to actual rights
• install/favorite/send stickers + personal packs from existing stickers
• creative UTF-8 files + existing media with captions
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
        self.mode = "disabled"

    @property
    def running(self) -> bool:
        return bool(self.application and self.application.running)

    async def start(self) -> None:
        if not self.settings.telegram_bot_token:
            log.info("Bot API disabled: TELEGRAM_BOT_TOKEN missing")
            self.mode = "disabled"
            return

        self.application = Application.builder().token(self.settings.telegram_bot_token).build()
        self.application.add_handler(TypeHandler(Update, self.handle_update))
        await self.application.initialize()
        await self.application.start()

        if self.settings.webhook_ready:
            self.mode = "webhook"
            url = self.settings.public_base_url.rstrip("/") + "/telegram/webhook"
            await self.application.bot.set_webhook(
                url=url,
                secret_token=self.settings.telegram_webhook_secret,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
            )
            log.info("Telegram Bot API webhook configured")
            return

        if not self.application.updater:
            raise RuntimeError("Telegram updater unavailable")
        self.mode = "polling"
        await self.application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
        log.info("Telegram Bot API polling started")

    async def stop(self) -> None:
        if not self.application:
            return
        if self.application.updater and self.application.updater.running:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
        self.application = None
        self.mode = "disabled"

    def valid_webhook_secret(self, supplied: str | None) -> bool:
        expected = self.settings.telegram_webhook_secret
        return bool(expected and supplied and secrets.compare_digest(expected, supplied))

    async def process_webhook(self, payload: dict) -> None:
        if not self.application or not self.application.running:
            raise RuntimeError("Telegram application is not running")
        update = Update.de_json(payload, self.application.bot)
        if update is not None:
            await self.application.process_update(update)

    def _is_owner(self, user_id: int | None) -> bool:
        return bool(
            user_id
            and self.settings.owner_telegram_id
            and user_id == self.settings.owner_telegram_id
        )

    async def _reply(
        self,
        update: Update,
        text: str,
        business_connection_id: str | None = None,
        reply_markup=None,
    ) -> None:
        msg = update.effective_message
        if not msg:
            return
        if self.application and business_connection_id:
            await BusinessBot(self.application.bot).send_text(
                msg.chat_id, text[:4000], business_connection_id, reply_to=msg.message_id
            )
        else:
            await msg.reply_text(text[:4000], reply_markup=reply_markup)

    async def react_to_message(
        self,
        chat_id: int,
        message_id: int,
        emoji: str | None = None,
        custom_emoji_id: str | None = None,
        remove: bool = False,
    ):
        if not self.application or not self.running:
            return {"ok": False, "error": "BotNotRunning"}
        if not remove and bool(emoji) == bool(custom_emoji_id):
            return {"ok": False, "error": "ChooseOneReaction"}
        try:
            reactions = (
                []
                if remove
                else [
                    ReactionTypeCustomEmoji(custom_emoji_id)
                    if custom_emoji_id
                    else ReactionTypeEmoji(emoji)
                ]
            )
            await self.application.bot.set_message_reaction(
                chat_id=chat_id, message_id=message_id, reaction=reactions
            )
            return {"ok": True, "actor": "bot", "removed": remove}
        except Exception as exc:
            log.warning("Bot reaction failed (%s)", type(exc).__name__)
            return {
                "ok": False,
                "error": type(exc).__name__,
                "message": "Telegram did not allow this bot reaction.",
            }

    async def _confirmation_callback(self, query) -> None:
        msg = query.message
        if not msg or msg.chat.type != "private" or not self._is_owner(query.from_user.id):
            await query.answer("Faqat owner tasdiqlashi mumkin.", show_alert=True)
            return
        parts = (query.data or "").split(":", 1)
        if len(parts) != 2 or parts[0] not in {"confirm", "cancel"}:
            await query.answer("Noma’lum amal.")
            return
        pending = self.confirmations.consume(msg.chat_id, parts[1])
        if not pending:
            await query.answer(
                "Tasdiqlash muddati tugagan yoki allaqachon ishlatilgan.", show_alert=True
            )
            return
        await query.answer()
        if parts[0] == "cancel":
            await query.edit_message_text("Amal bekor qilindi.")
            return
        try:
            result = await self.writes.execute_confirmed(pending.action, pending.arguments)
            outcome = (
                "Tasdiqlangan amal bajarildi."
                if not isinstance(result, dict) or result.get("ok", True)
                else "Amal to‘liq tugamadi. Bajarilgan qismlar avtomatik takrorlanmaydi."
            )
        except Exception as exc:
            log.warning("Confirmed action %s failed (%s)", pending.action, type(exc).__name__)
            outcome = (
                "Amal yakunlangani tasdiqlanmadi. Avtomatik takrorlanmadi; holatni tekshiring."
            )
        # A reply-edit failure must never re-execute an already consumed action or
        # misreport a successful mutation as a failed Telegram operation.
        try:
            await query.edit_message_text(outcome)
        except Exception as exc:
            log.warning("Confirmation reply failed (%s)", type(exc).__name__)

    async def handle_update(self, update: Update, _context) -> None:
        try:
            if getattr(update, "callback_query", None):
                await self._confirmation_callback(update.callback_query)
                return
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
            is_owner = self._is_owner(user_id) and msg.chat.type == "private" and not is_business
            text = (msg.text or msg.caption or "").strip()

            if is_owner and text.casefold() in {
                "tasdiqlayman",
                "ha tasdiqlayman",
                "ha, tasdiqlayman",
                "confirm",
            }:
                pending = self.confirmations.for_chat(msg.chat_id)
                if len(pending) != 1:
                    await msg.reply_text(
                        "Bitta aniq amal tanlanmadi. Kerakli Tasdiqlash tugmasini bosing."
                    )
                    return
                await self._handle_command(
                    update, f"/confirm {pending[0].token}", True, natural=True
                )
                return

            if text.startswith("/") and not is_business:
                await self._handle_command(update, text, is_owner)
                return
            if is_business and not self.settings.auto_reply_business:
                return

            media_note = ""
            if msg.voice or msg.audio or msg.photo or msg.document or msg.video:
                media_note = (
                    await self._understand_media(msg) if self.gemini.ready else "[Media attached]"
                )
            prompt = text
            if media_note:
                prompt = (text + "\n\nAttached media understanding:\n" + media_note).strip()
            sticker = getattr(msg, "sticker", None)
            if sticker:
                prompt += f"\nAttached sticker metadata: set_name={sticker.set_name}, emoji={sticker.emoji}."
            if not prompt:
                prompt = "Respond appropriately to the attached media."

            allow_account_tools = is_owner and not is_business
            previous_tokens = {p.token for p in self.confirmations.for_chat(msg.chat_id)}
            response = await self.agent.chat(
                chat_id=msg.chat_id,
                text=prompt,
                allow_account_tools=allow_account_tools,
                message_id=getattr(msg, "message_id", None) if not is_business else None,
            )
            pending = [
                p
                for p in self.confirmations.for_chat(msg.chat_id)
                if p.token not in previous_tokens
            ]
            buttons = [
                [
                    InlineKeyboardButton(
                        "Tasdiqlash" if len(pending) == 1 else f"Tasdiqlash ({i + 1})",
                        callback_data=f"confirm:{p.token}",
                    ),
                    InlineKeyboardButton("Bekor qilish", callback_data=f"cancel:{p.token}"),
                ]
                for i, p in enumerate(pending[:5])
            ]
            await self._reply(
                update,
                response,
                business_connection_id,
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )
        except GeminiError as exc:
            log.warning("AI provider unavailable status=%s", exc.status_code)
            reason = (
                "AI ulanishi loyiha billing/kredit holati sabab bloklangan. "
                if exc.status_code == 402
                else "AI ulanishi kalit yoki ruxsat sabab bloklangan. "
                if exc.status_code in {401, 403}
                else "AI hozir band yoki vaqtincha javob bermayapti. "
            )
            await self._reply(
                update,
                reason + "Buyruq yozishingiz kerak emas. Bu vazifa to‘liq bajarilganini "
                "tasdiqlay olmayman; amallarni avtomatik takrorlamayman. "
                "Ulanish tiklangach, yana oddiy gap bilan yozishingiz mumkin.",
                business_connection_id,
            )
        except Exception:
            log.exception("Update handling failed")
            try:
                await self._reply(
                    update,
                    "Ichki xatolik yuz berdi. Vazifa yakunlangani tasdiqlanmadi. "
                    "Buyruq yozishingiz shart emas; amallar avtomatik takrorlanmadi.",
                )
            except Exception:
                pass

    async def _handle_command(
        self, update: Update, text: str, is_owner: bool, *, natural: bool = False
    ) -> None:
        msg = update.effective_message
        assert msg is not None
        parts = text.split()
        command = parts[0].split("@", 1)[0].lower()

        if command in {"/start", "/help"}:
            await msg.reply_text(HELP_TEXT)
            return
        if command == "/whoami":
            user_id = update.effective_user.id if update.effective_user else None
            await msg.reply_text(f"Telegram ID: {user_id}\nOwner: {'YES' if is_owner else 'NO'}")
            return
        if command == "/tools":
            await msg.reply_text(TOOLS_TEXT)
            return
        if command == "/actions":
            await msg.reply_text(
                "Use /do ACTION {JSON}. Every direct change requires /confirm TOKEN within 5 minutes. "
                "Use /cancel to discard it. Numeric chat IDs, @usernames and me (Saved Messages) are accepted.\n\n"
                'Examples:\n/do send_message {"peer":"me","text":"Hello"}\n'
                '/do edit_message {"peer":"me","message_id":123,"text":"Updated"}\n'
                '/do delete_messages {"peer":"me","message_ids":[123],"revoke":true}\n'
                '/do archive_chat {"peer":"@username","archived":true}\n'
                '/do mute_chat {"peer":"@username","minutes":60}\n\n'
                "Actions: " + ", ".join(self.writes.ACTION_METHODS)
            )
            return
        if command == "/status":
            status = self.settings.setup_status()
            status["bot_running"] = self.running
            status["bot_mode"] = self.mode
            status["owner_bound"] = bool(self.settings.owner_telegram_id)
            status["memory_backend"] = self.db.backend
            status["mtproto_running"] = self.writes.mt.ready
            status["account_access"] = is_owner and self.writes.mt.ready
            status["ai_note"] = "Configured only; provider availability is not tested by /status"
            status["ai_last_http_status"] = getattr(self.gemini, "last_http_status", None)
            if is_owner and self.writes.mt.ready:
                account = await self.writes.mt.me()
                status["account_id"] = account["id"]
                status["owner_matches_account"] = account["id"] == self.settings.owner_telegram_id
            await msg.reply_text("\n".join(f"{k}: {v}" for k, v in status.items()))
            return
        if command in {"/account", "/chats", "/messages", "/search", "/contacts", "/do"}:
            if not is_owner:
                await msg.reply_text(
                    "Account tools are available only to the owner in a private bot chat."
                )
                return
            await self._account_command(msg, command, text)
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
            try:
                result = await self.writes.execute_confirmed(pending.action, pending.arguments)
                if natural:
                    await msg.reply_text(
                        "Tasdiqlangan amal bajarildi."
                        if not isinstance(result, dict) or result.get("ok", True)
                        else "Telegram amalni bajarmadi. Cheklov/huquqlarni tekshiring."
                    )
                else:
                    await msg.reply_text(f"Confirmed: {pending.action}\n{result}")
            except Exception as exc:
                log.warning("Confirmed action %s failed (%s)", pending.action, type(exc).__name__)
                await msg.reply_text(
                    f"Action failed: {type(exc).__name__}. Token consumed; no automatic retry. Check /status."
                )
            return

        await msg.reply_text("Unknown command. /help")

    @staticmethod
    def _peer(value: str) -> str | int:
        return int(value) if value.lstrip("-").isdigit() else value

    @staticmethod
    async def _account_result(msg, result) -> None:
        # Bound output without silently cutting a record in the middle.
        records = result if isinstance(result, list) else [result]
        page = ""
        pages = 0
        for index, record in enumerate(records):
            line = redact_text(json.dumps(record, ensure_ascii=False, default=str))
            if len(line) > 3500:
                line = line[:3450] + " ... [record truncated]"
            if page and len(page) + len(line) + 1 > 3500:
                await msg.reply_text(page)
                pages += 1
                page = ""
                if pages >= 4:
                    await msg.reply_text(
                        f"Output limited; {len(records) - index} remaining. Use a smaller limit/query."
                    )
                    return
            page += ("\n" if page else "") + line
        await msg.reply_text(page or "No results.")

    async def _account_command(self, msg, command: str, text: str) -> None:
        if not self.writes.mt.ready:
            await msg.reply_text("MTProto is disconnected. /status")
            return
        parts = text.split()
        try:
            if command == "/account":
                result = await self.writes.mt.me()
            elif command == "/chats":
                limit = max(1, min(int(parts[1]) if len(parts) > 1 else 20, 100))
                result = await self.writes.mt.list_dialogs(limit)
            elif command == "/contacts":
                limit = max(1, min(int(parts[1]) if len(parts) > 1 else 20, 100))
                result = (await self.writes.mt.contacts())[:limit]
            elif command == "/messages":
                if len(parts) < 2:
                    raise ValueError("Usage: /messages PEER [limit]")
                limit = max(1, min(int(parts[2]) if len(parts) > 2 else 10, 100))
                result = await self.writes.mt.recent_messages(self._peer(parts[1]), limit)
            elif command == "/search":
                query = text.partition(" ")[2].strip()
                if not query:
                    raise ValueError("Usage: /search QUERY")
                result = await self.writes.mt.global_search(query, 20)
            else:
                action_parts = text.split(maxsplit=2)
                if len(action_parts) != 3 or action_parts[1] not in self.writes.ACTION_METHODS:
                    raise ValueError("Usage: /do ACTION JSON. See /actions for examples.")
                action = action_parts[1]
                args = json.loads(action_parts[2])
                if not isinstance(args, dict):
                    raise ValueError("JSON must be an object.")
                for key in ("peer", "target", "source"):
                    if isinstance(args.get(key), str):
                        args[key] = self._peer(args[key])
                method = getattr(self.writes.mt, self.writes.ACTION_METHODS[action])
                inspect.signature(method).bind(**args)
                pending = self.confirmations.create(msg.chat_id, action, args)
                result = {
                    "confirmation_required": True,
                    "action": action,
                    "arguments": args,
                    "confirm": f"/confirm {pending.token}",
                    "expires_in_seconds": self.confirmations.ttl_seconds,
                }
            await self._account_result(msg, result)
        except (ValueError, TypeError):
            await msg.reply_text(
                "Invalid arguments. /help or /actions for syntax. No account change was made."
            )
        except MTProtoUnavailable:
            await msg.reply_text("MTProto is disconnected. /status")
        except Exception as exc:
            log.warning("Direct account command %s failed (%s)", command, type(exc).__name__)
            await msg.reply_text(
                f"Telegram action failed: {type(exc).__name__}. No automatic retry; check /status."
            )

    async def _understand_media(self, msg) -> str:
        tg_file = None
        mime = "application/octet-stream"
        if msg.voice:
            tg_file = await msg.voice.get_file()
            mime = msg.voice.mime_type or "audio/ogg"
        elif msg.audio:
            tg_file = await msg.audio.get_file()
            mime = msg.audio.mime_type or "audio/mpeg"
        elif msg.video:
            tg_file = await msg.video.get_file()
            mime = msg.video.mime_type or "video/mp4"
        elif msg.document:
            tg_file = await msg.document.get_file()
            mime = msg.document.mime_type or mime
        elif msg.photo:
            tg_file = await msg.photo[-1].get_file()
            mime = "image/jpeg"
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
