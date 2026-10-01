from __future__ import annotations

from typing import Any

from telegram import Bot


class BusinessBot:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_text(self, chat_id: int, text: str, business_connection_id: str | None = None, reply_to: int | None = None):
        kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if business_connection_id:
            kwargs["business_connection_id"] = business_connection_id
        if reply_to:
            kwargs["reply_parameters"] = {"message_id": reply_to}
        return await self.bot.send_message(**kwargs)

    async def send_document(self, chat_id: int, path: str, business_connection_id: str | None = None, caption: str | None = None):
        kwargs: dict[str, Any] = {"chat_id": chat_id, "document": open(path, "rb"), "caption": caption}
        if business_connection_id:
            kwargs["business_connection_id"] = business_connection_id
        return await self.bot.send_document(**kwargs)
