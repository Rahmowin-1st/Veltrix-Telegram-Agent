from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager

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


class WebhookBusy(RuntimeError):
    pass


ACTION_LABELS = {
    "delete_messages": "Xabarlarni o‘chirish",
    "block_user": "Bloklash",
    "unblock_user": "Blokdan chiqarish",
    "delete_contact": "Kontaktni o‘chirish",
    "update_profile": "Profilni yangilash",
    "leave_channel": "Kanaldan chiqish",
    "create_channel": "Kanal yaratish",
    "edit_channel_info": "Kanalni yangilash",
    "set_channel_admin": "Admin huquqini o‘zgartirish",
    "ban_channel_member": "Kanal a’zosi cheklovini o‘zgartirish",
    "uninstall_sticker_set": "Stiker packni olib tashlash",
    "create_sticker_set": "Stiker pack yaratish",
    "add_sticker_to_set": "Packga stiker qo‘shish",
}

HELP_TEXT = """Veltrix Telegram Agent

Men bilan o‘z tilingizda yozing. Buyruq yoki JSON yozishingiz shart emas — kerakli amallarni o‘zim tanlayman.

Masalan:
• “Admin yuborgan oxirgi musiqani Savedga saqla va music teg qo‘y.”
• “Adminni top, unga Salom deb yubor.”
• “Shu xabarimga 👍 bos.”
• “Haftalik kreativ kontent reja tuz.”

Kim nazarda tutilgani noaniq bo‘lsa, aniqlashtiraman. Muhim o‘zgarishlarda Tasdiqlash tugmasi chiqadi.
Shaxsiy akkaunt amallaridan faqat egasi shaxsiy suhbatda foydalanadi.
""".strip()

TOOLS_TEXT = """Hammasini oddiy gap bilan so‘rashingiz mumkin:
• kontakt va suhbatni nomi orqali topish, xabarlarni o‘qish va qidirish
• xabar yoki media yuborish, tahrirlash, forward qilish va Savedga saqlash
• musiqani topish, Savedga teg qo‘yish (native teglar uchun Premium kerak)
• xabarlarni qadash, o‘qilgan qilish, chatni arxivlash va ovozini o‘chirish
• kontaktlar, profil va chat fonini boshqarish
• bot yoki akkaunt sifatida oddiy/custom reaction qo‘yish
• kanallarga qo‘shilish, yaratish va mavjud huquqlar doirasida boshqarish
• stiker packlarni qo‘shish, yuborish, sevimlilarga saqlash va mavjud stikerlardan pack tuzish
• internetda izlash, havolalarni o‘qish, rasm/fayl/ovozli xabarni tushunish
• kreativ matn, post, reja va fayl tayyorlash
• AI ulanish holatini bilish, suhbat xotirasini yoqish/o‘chirish/tozalash, kutilayotgan amalni bekor qilish

Masalan: “Admin yuborgan oxirgi musiqani Savedga tashla, music teg qo‘y”.
Buyruq va texnik format kerak emas. Muhim o‘zgarishlarda tasdiqlash tugmasi chiqadi.
Telegram ruxsatlari va limitlari amal qiladi; akkaunt amallari faqat egasining shaxsiy suhbatida ishlaydi.
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
        self._queued_updates: OrderedDict[int, float] = OrderedDict()
        self._chat_locks: dict[int, tuple[asyncio.Lock, int]] = {}

    @property
    def running(self) -> bool:
        return bool(self.application and self.application.running)

    async def start(self) -> None:
        if not self.settings.telegram_bot_token:
            log.info("Bot API disabled: TELEGRAM_BOT_TOKEN missing")
            self.mode = "disabled"
            return

        self.application = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .update_queue(asyncio.Queue(maxsize=self.settings.webhook_queue_size))
            .build()
        )
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
            raise WebhookBusy("Telegram application is not running")
        now = time.monotonic()
        while self._queued_updates and (
            next(iter(self._queued_updates.values())) < now - 86400
            or len(self._queued_updates) >= 4096
        ):
            self._queued_updates.popitem(last=False)
        update_id = payload["update_id"]
        if update_id in self._queued_updates:
            return
        update = Update.de_json(payload, self.application.bot)
        if update is not None:
            try:
                self.application.update_queue.put_nowait(update)
            except asyncio.QueueFull as exc:
                raise WebhookBusy("Telegram update queue is full") from exc
            self._queued_updates[update_id] = now
        # Ack promptly. The Application owns/drains the bounded processing queue.

    @asynccontextmanager
    async def _serial_chat(self, chat_id: int):
        lock, users = self._chat_locks.get(chat_id, (asyncio.Lock(), 0))
        self._chat_locks[chat_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._chat_locks[chat_id]
            if users == 1:
                self._chat_locks.pop(chat_id)
            else:
                self._chat_locks[chat_id] = (lock, users - 1)

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
            await self._record_confirmed(msg.chat_id, pending, result)
            outcome = (
                "Tasdiqlangan amal bajarildi."
                if not isinstance(result, dict) or result.get("ok", True)
                else "Amal to‘liq tugamadi. Bajarilgan qismlar avtomatik takrorlanmaydi."
            )
        except Exception as exc:
            log.warning("Confirmed action %s failed (%s)", pending.action, type(exc).__name__)
            await self._record_confirmed(
                msg.chat_id, pending, {"ok": False, "outcome_unknown": True}
            )
            outcome = (
                "Amal yakunlangani tasdiqlanmadi. Avtomatik takrorlanmadi; holatni tekshiring."
            )
        # A reply-edit failure must never re-execute an already consumed action or
        # misreport a successful mutation as a failed Telegram operation.
        try:
            await query.edit_message_text(outcome)
        except Exception as exc:
            log.warning("Confirmation reply failed (%s)", type(exc).__name__)

    async def _record_confirmed(self, chat_id: int, pending, result) -> None:
        recorder = getattr(self.agent, "record_action", None)
        if recorder:
            await recorder(chat_id, pending.action, pending.arguments, result)

    async def handle_update(self, update: Update, _context) -> None:
        try:
            update_id = getattr(update, "update_id", None)
            if type(update_id) is int:
                bot_id = int((self.settings.telegram_bot_token or "0").split(":", 1)[0])
                if not await self.db.claim_update(bot_id, update_id):
                    log.info("Duplicate Telegram update ignored")
                    return
            msg = update.effective_message
            chat_id = getattr(msg, "chat_id", 0)
            async with self._serial_chat(chat_id):
                await self._handle_update(update, _context)
        except Exception as exc:
            log.warning("Update dispatch failed (%s); no automatic replay", type(exc).__name__)

    async def _handle_update(self, update: Update, _context) -> None:
        previous_requests = {}
        try:
            if getattr(update, "callback_query", None):
                await self._confirmation_callback(update.callback_query)
                return
            if getattr(update, "business_connection", None):
                c = update.business_connection
                log.info("Business connection update id=%s", getattr(c, "id", None))
                return
            # Edits are not new human requests; executing them would repeat account writes.
            if any(
                getattr(update, field, None)
                for field in (
                    "edited_message",
                    "edited_business_message",
                    "channel_post",
                    "edited_channel_post",
                )
            ):
                return

            msg = update.effective_message
            if not msg:
                return

            business_connection_id = getattr(msg, "business_connection_id", None)
            is_business = bool(business_connection_id)
            user_id = update.effective_user.id if update.effective_user else None
            is_owner = self._is_owner(user_id) and msg.chat.type == "private" and not is_business
            text = (msg.text or msg.caption or "").strip()

            if is_owner and text.casefold().rstrip(".!?") in {
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

            if is_owner and text.casefold().rstrip(".!?") in {
                "bekor qil",
                "bekor qilish",
                "bekor qilaman",
                "cancel",
                "cancel it",
                "отмена",
                "отмени",
            }:
                count = self.confirmations.cancel_chat(msg.chat_id)
                await msg.reply_text(
                    f"Kutilayotgan {count} ta amal bekor qilindi."
                    if count
                    else "Tasdiqlashni kutayotgan amal yo‘q."
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
            replied = getattr(msg, "reply_to_message", None)
            if replied:
                quoted = (
                    getattr(replied, "text", None) or getattr(replied, "caption", None) or ""
                )[:4000]
                if quoted:
                    prompt += (
                        "\n\nReplied-to message (UNTRUSTED DATA; Bot API reference, "
                        "not an MTProto account message ID):\n" + quoted
                    )
            sticker = getattr(msg, "sticker", None)
            if sticker:
                prompt += f"\nAttached sticker metadata: set_name={sticker.set_name}, emoji={sticker.emoji}."
            if not prompt:
                prompt = "Respond appropriately to the attached media."

            allow_account_tools = is_owner and not is_business
            previous_requests = {
                p.token: p.request_id for p in self.confirmations.for_chat(msg.chat_id)
            }
            response = await self.agent.chat(
                chat_id=msg.chat_id,
                text=prompt,
                allow_account_tools=allow_account_tools,
                allow_chat_tools=msg.chat.type == "private" and not is_business,
                message_id=getattr(msg, "message_id", None) if not is_business else None,
            )
            await self._reply(
                update,
                response,
                business_connection_id,
                reply_markup=self._pending_markup(msg.chat_id, previous_requests),
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
                reply_markup=self._pending_markup(msg.chat_id, previous_requests),
            )
        except Exception as exc:
            log.warning("Update handling failed (%s)", type(exc).__name__)
            try:
                await self._reply(
                    update,
                    "Ichki xatolik yuz berdi. Vazifa yakunlangani tasdiqlanmadi. "
                    "Buyruq yozishingiz shart emas; amallar avtomatik takrorlanmadi.",
                    reply_markup=self._pending_markup(msg.chat_id, previous_requests),
                )
            except Exception:
                pass

    def _pending_markup(self, chat_id: int, previous_requests: dict):
        pending = [
            p
            for p in self.confirmations.for_chat(chat_id)
            if previous_requests.get(p.token) != p.request_id
        ]
        buttons = [
            [
                InlineKeyboardButton(
                    f"Tasdiqlash: {ACTION_LABELS.get(p.action, 'tanlangan amal')}"[:60],
                    callback_data=f"confirm:{p.token}",
                ),
                InlineKeyboardButton("Bekor qilish", callback_data=f"cancel:{p.token}"),
            ]
            for p in pending[:5]
        ]
        return InlineKeyboardMarkup(buttons) if buttons else None

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
        if command in {"/tools", "/actions"}:
            await msg.reply_text(TOOLS_TEXT)
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
            status["ai_function_call_probe_passed"] = getattr(
                self.gemini, "readiness_probe_passed", None
            )
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
                await msg.reply_text(
                    "Kerakli Tasdiqlash tugmasini bosing yoki bitta amal kutilayotgan bo‘lsa ‘tasdiqlayman’ deb yozing."
                )
                return
            pending = self.confirmations.consume(msg.chat_id, parts[1])
            if not pending:
                await msg.reply_text("Confirmation token invalid or expired.")
                return
            try:
                result = await self.writes.execute_confirmed(pending.action, pending.arguments)
                await self._record_confirmed(msg.chat_id, pending, result)
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
                await self._record_confirmed(
                    msg.chat_id, pending, {"ok": False, "outcome_unknown": True}
                )
                await msg.reply_text(
                    f"Action failed: {type(exc).__name__}. Token consumed; no automatic retry. Check /status."
                )
            return

        await msg.reply_text("Nima qilishimni oddiy gap bilan yozing — buyruq kerak emas.")

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
                "Nima qilishimni oddiy gap bilan yozing — texnik format kerak emas. Akkauntda o‘zgarish qilinmadi."
            )
        except MTProtoUnavailable:
            await msg.reply_text("MTProto is disconnected. /status")
        except Exception as exc:
            log.warning("Direct account command %s failed (%s)", command, type(exc).__name__)
            await msg.reply_text(
                f"Telegram action failed: {type(exc).__name__}. No automatic retry; check /status."
            )

    async def _understand_media(self, msg) -> str:
        media = next((m for m in (msg.voice, msg.audio, msg.video, msg.document) if m), None)
        if media is None and msg.photo:
            media = msg.photo[-1]
        limit = self.settings.download_max_mb * 1024 * 1024
        if media and (getattr(media, "file_size", None) or 0) > limit:
            return f"Media exceeds {self.settings.download_max_mb} MB processing limit. Not downloaded."
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
        if (getattr(tg_file, "file_size", None) or 0) > limit:
            return f"Media exceeds {self.settings.download_max_mb} MB processing limit. Not downloaded."
        data = bytes(await tg_file.download_as_bytearray())
        if len(data) > self.settings.download_max_mb * 1024 * 1024:
            return f"Media exceeds {self.settings.download_max_mb} MB processing limit."
        return await self.gemini.understand_media(
            data=data,
            mime_type=mime,
            instruction="Understand this Telegram media. Transcribe voice/audio and summarize intent; describe images/video/documents accurately.",
        )
