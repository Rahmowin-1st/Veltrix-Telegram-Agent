from __future__ import annotations

from typing import Any

from app.telegram.mtproto_client import MTProtoClient


class TelegramReadTools:
    def __init__(self, mt: MTProtoClient):
        self.mt = mt

    async def account_info(self, **_: Any):
        return await self.mt.me()

    async def recent_chats(self, limit: int = 20, **_: Any):
        return await self.mt.list_dialogs(limit)

    async def search_telegram(self, query: str, limit: int = 20, **_: Any):
        return await self.mt.search_entities(query, limit)

    async def recent_messages(self, peer: str, limit: int = 20, **_: Any):
        return await self.mt.recent_messages(peer, limit)

    async def search_messages(self, peer: str, query: str, limit: int = 20, **_: Any):
        return await self.mt.search_messages(peer, query, limit)

    async def global_message_search(self, query: str, limit: int = 20, **_: Any):
        return await self.mt.global_search(query, limit)

    async def list_contacts(self, **_: Any):
        return await self.mt.contacts()

    async def entity_info(self, peer: str, **_: Any):
        return await self.mt.get_entity_info(peer)
