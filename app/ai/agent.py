from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.ai.extended_schemas import EXTENDED_READS
from app.ai.gemini_client import GeminiClient
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import TOOL_DECLARATIONS
from app.config import Settings
from app.db import Database
from app.security.redaction import SENSITIVE_KEYS, redact_text
from app.telegram.mtproto_client import MTProtoUnavailable
from app.tools.media_tools import download_public_media
from app.tools.telegram_read import TelegramReadTools
from app.tools.telegram_write import TelegramWriteTools
from app.tools.web_tools import fetch_public_url, platform_query

log = logging.getLogger(__name__)

ToolFunc = Callable[..., Awaitable[Any]]
PUBLIC_TOOLS = frozenset({"web_search", "fetch_url", "download_media", "react_to_user_message"})


class Agent:
    def _safe_data(self, value):
        if isinstance(value, str):
            result = redact_text(value)
            for key in (
                "gemini_api_key",
                "telegram_bot_token",
                "telegram_api_hash",
                "telegram_session",
            ):
                secret = getattr(self.settings, key, None)
                if secret:
                    result = result.replace(secret, "[REDACTED]")
            return result[:6000]
        if isinstance(value, list):
            return [self._safe_data(item) for item in value[:100]]
        if isinstance(value, dict):
            return {
                key: "[REDACTED]"
                if key.lower() in SENSITIVE_KEYS or key.lower() == "token"
                else self._safe_data(item)
                for key, item in value.items()
            }
        return value

    def __init__(
        self,
        settings: Settings,
        db: Database,
        gemini: GeminiClient,
        reads: TelegramReadTools,
        writes: TelegramWriteTools,
    ):
        self.settings = settings
        self.db = db
        self.gemini = gemini
        self.reads = reads
        self.writes = writes
        self.bot_reactor = None
        self.tool_map: dict[str, ToolFunc] = {
            "account_info": self.reads.account_info,
            "recent_chats": self.reads.recent_chats,
            "search_telegram": self.reads.search_telegram,
            "recent_messages": self.reads.recent_messages,
            "search_messages": self.reads.search_messages,
            "global_message_search": self.reads.global_message_search,
            "list_contacts": self.reads.list_contacts,
            "entity_info": self.reads.entity_info,
            "web_search": self._web_search,
            "fetch_url": self._fetch_url,
            "download_media": self._download_media,
            "send_message": self.writes.send_message,
            "edit_message": self.writes.edit_message,
            "delete_messages": self.writes.delete_messages,
            "forward_message": self.writes.forward_message,
            "pin_message": self.writes.pin_message,
            "unpin_message": self.writes.unpin_message,
            "mark_read": self.writes.mark_read,
            "archive_chat": self.writes.archive_chat,
            "mute_chat": self.writes.mute_chat,
            "unmute_chat": self.writes.unmute_chat,
            "block_user": self.writes.block_user,
            "unblock_user": self.writes.unblock_user,
            "add_contact": self.writes.add_contact,
            "import_contact": self.writes.import_contact,
            "delete_contact": self.writes.delete_contact,
            "update_profile": self.writes.update_profile,
            "set_chat_wallpaper": self.writes.set_chat_wallpaper,
        }
        for name, method in EXTENDED_READS.items():
            self.tool_map[name] = getattr(self.reads.mt, method)
        for name in writes.ACTION_METHODS:
            if name not in self.tool_map:
                self.tool_map[name] = writes.handler_for(name)

    async def _web_search(self, query: str, platform: str | None = None, **_: Any):
        effective = platform_query(query, platform)
        answer = await self.gemini.research(
            "Search the live public web and answer with concise factual findings and source URLs. "
            "Do not claim access to private/logged-in content. Query: " + effective
        )
        return {"query": effective, "answer": answer}

    async def _fetch_url(self, url: str, **_: Any):
        return await fetch_public_url(url)

    async def _download_media(self, url: str, **_: Any):
        return await download_public_media(url, max_mb=self.settings.download_max_mb)

    async def _execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        owner_chat_id: int,
        *,
        allow_account_tools: bool = False,
        message_id: int | None = None,
    ) -> Any:
        if not isinstance(name, str) or not isinstance(args, dict):
            return {"error": "InvalidFunctionCall"}
        # Enforce authority at dispatch, not just in the declarations sent to the model.
        if name not in PUBLIC_TOOLS and not allow_account_tools:
            return {"error": "AccountAccessDenied"}
        if name == "react_to_user_message":
            if not self.bot_reactor or message_id is None:
                return {"error": "BotReactionUnavailable"}
            # The model cannot choose another user's chat/message or inject target identifiers.
            allowed = {k: v for k, v in args.items() if k in {"emoji", "custom_emoji_id", "remove"}}
            return await self.bot_reactor(owner_chat_id, message_id, **allowed)
        tool = self.tool_map.get(name)
        if not tool:
            return {"error": "UnknownTool"}
        if name in self.writes.ACTION_METHODS:
            args = {**args, "owner_chat_id": owner_chat_id}
        try:
            return await tool(**args)
        except MTProtoUnavailable as exc:
            return {"error": str(exc), "needs_mtproto": True}
        except Exception as exc:
            log.warning("Tool %s failed (%s)", name, type(exc).__name__)
            result = {
                "error": type(exc).__name__,
                "message": "Tool failed. Do not claim success or retry writes.",
            }
            if hasattr(exc, "seconds"):
                result["retry_after_seconds"] = exc.seconds
            return result

    async def chat(
        self, *, chat_id: int, text: str, allow_account_tools: bool, message_id: int | None = None
    ) -> str:
        if not self.gemini.ready:
            return (
                "AI ulanishi hali sozlanmagan. Buyruq yozishingiz kerak emas; "
                "ulanish tiklangach, men bilan oddiy gap orqali ishlaysiz."
            )

        history = await self.db.get_history(chat_id, self.settings.max_history_messages)
        contents: list[dict[str, Any]] = []
        for item in history:
            role = "model" if item["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": self._safe_data(item["content"])}]})
        text = self._safe_data(text)
        contents.append({"role": "user", "parts": [{"text": text}]})

        await self.db.add_memory(chat_id, "user", text)

        declarations = (
            TOOL_DECLARATIONS
            if allow_account_tools
            else [d for d in TOOL_DECLARATIONS if d["name"] in PUBLIC_TOOLS]
        )
        if message_id is None:
            declarations = [d for d in declarations if d["name"] != "react_to_user_message"]

        final_text = ""
        mutation_results = {}
        for _step in range(self.settings.max_agent_steps):
            response = await self.gemini.agent_turn(
                system_prompt=SYSTEM_PROMPT,
                contents=contents,
                function_declarations=declarations,
            )
            model_content = self.gemini.model_content_from_response(response)
            calls = self.gemini.extract_function_calls(response)
            text_chunk = self.gemini.extract_text(response)
            contents.append(model_content)

            if not calls:
                final_text = text_chunk or "AI javobi bo‘sh qaytdi. Iltimos qayta urinib ko‘ring."
                break

            response_parts = []
            for call in calls:
                key = json.dumps([call["name"], call["args"]], sort_keys=True, default=str)
                is_write = (
                    call["name"] in self.writes.ACTION_METHODS
                    or call["name"] == "react_to_user_message"
                )
                if is_write and key in mutation_results:
                    result = mutation_results[key]
                else:
                    result = await self._execute_tool(
                        call["name"],
                        call["args"],
                        chat_id,
                        allow_account_tools=allow_account_tools,
                        message_id=message_id,
                    )
                    if is_write:
                        mutation_results[key] = result
                function_response = {
                    "name": call["name"],
                    "response": {"result": self._safe_data(result)},
                }
                if call.get("id"):
                    function_response["id"] = call["id"]
                response_parts.append({"functionResponse": function_response})
                # Metadata-only trace: never log private messages, tool arguments, tokens, or keys.
                log.info(
                    "Agent tool=%s outcome=%s",
                    call["name"]
                    if call["name"] in self.tool_map or call["name"] == "react_to_user_message"
                    else "unknown",
                    "confirmation"
                    if isinstance(result, dict) and result.get("confirmation_required")
                    else "error"
                    if isinstance(result, dict)
                    and (result.get("error") or result.get("ok") is False)
                    else "success",
                )
            contents.append({"role": "user", "parts": response_parts})
        else:
            final_text = "Vazifa to‘liq tugamadi: bosqich limiti tugadi. Bajarilgan amallarni takrorlamasdan davom ettirish uchun topshiriqni aniqlashtiring."

        final_text = self._safe_data(final_text)
        await self.db.add_memory(chat_id, "assistant", final_text)
        return final_text
