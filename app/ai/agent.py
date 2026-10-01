from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.ai.gemini_client import GeminiClient
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import TOOL_DECLARATIONS
from app.config import Settings
from app.db import Database
from app.telegram.mtproto_client import MTProtoUnavailable
from app.tools.media_tools import download_public_media
from app.tools.telegram_read import TelegramReadTools
from app.tools.telegram_write import TelegramWriteTools
from app.tools.web_tools import fetch_public_url, platform_query

log = logging.getLogger(__name__)

ToolFunc = Callable[..., Awaitable[Any]]
PUBLIC_TOOLS = frozenset({"web_search", "fetch_url", "download_media"})


class Agent:
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
        self, name: str, args: dict[str, Any], owner_chat_id: int, *, allow_account_tools: bool = False
    ) -> Any:
        # Enforce authority at dispatch, not just in the declarations sent to the model.
        if name not in PUBLIC_TOOLS and not allow_account_tools:
            return {"error": "AccountAccessDenied"}
        tool = self.tool_map.get(name)
        if not tool:
            return {"error": "UnknownTool"}
        if name in {
            "send_message", "edit_message", "delete_messages", "forward_message", "pin_message", "unpin_message",
            "mark_read", "archive_chat", "mute_chat", "unmute_chat", "block_user", "unblock_user", "add_contact",
            "import_contact", "delete_contact", "update_profile", "set_chat_wallpaper",
        }:
            args = {**args, "owner_chat_id": owner_chat_id}
        try:
            return await tool(**args)
        except MTProtoUnavailable as exc:
            return {"error": str(exc), "needs_mtproto": True}
        except Exception as exc:
            log.exception("Tool %s failed", name)
            return {"error": type(exc).__name__, "message": "Tool failed; check server diagnostics."}

    async def chat(self, *, chat_id: int, text: str, allow_account_tools: bool) -> str:
        if not self.gemini.ready:
            return "Gemini API hali sozlanmagan. /status orqali ko‘ring."

        history = await self.db.get_history(chat_id, self.settings.max_history_messages)
        contents: list[dict[str, Any]] = []
        for item in history:
            role = "model" if item["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": item["content"]}]})
        contents.append({"role": "user", "parts": [{"text": text}]})

        await self.db.add_memory(chat_id, "user", text)

        declarations = TOOL_DECLARATIONS if allow_account_tools else [
            d for d in TOOL_DECLARATIONS if d["name"] in PUBLIC_TOOLS
        ]

        final_text = ""
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
                final_text = text_chunk or "Bajarildi."
                break

            response_parts = []
            for call in calls:
                result = await self._execute_tool(
                    call["name"], call["args"], chat_id, allow_account_tools=allow_account_tools
                )
                response_parts.append({
                    "functionResponse": {
                        "name": call["name"],
                        "response": {"result": result},
                    }
                })
            contents.append({"role": "user", "parts": response_parts})
        else:
            final_text = "Agent step limiti tugadi. Oxirgi tool natijalari saqlandi; vazifani kichikroq bo‘lib davom ettiring."

        await self.db.add_memory(chat_id, "assistant", final_text)
        return final_text
