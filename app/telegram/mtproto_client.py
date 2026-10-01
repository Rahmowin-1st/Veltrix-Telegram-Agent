from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from app.config import Settings

log = logging.getLogger(__name__)


class MTProtoUnavailable(RuntimeError):
    pass


class MTProtoClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: TelegramClient | None = None

    @property
    def ready(self) -> bool:
        return bool(self.client and self.client.is_connected())

    async def start(self) -> None:
        if not self.settings.has_mtproto:
            log.info("MTProto disabled: credentials/session incomplete")
            return
        self.client = TelegramClient(
            StringSession(self.settings.telegram_session),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
            auto_reconnect=True,
            connection_retries=10,
            retry_delay=2,
        )
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            self.client = None
            raise MTProtoUnavailable("TELEGRAM_SESSION is not authorized")
        me = await self.client.get_me()
        log.info("MTProto connected as user_id=%s", getattr(me, "id", None))

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None

    def _require(self) -> TelegramClient:
        if not self.client or not self.client.is_connected():
            raise MTProtoUnavailable("MTProto user session is not configured/connected")
        return self.client

    async def me(self) -> dict[str, Any]:
        me = await self._require().get_me()
        return {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": "hidden",
            "premium": bool(getattr(me, "premium", False)),
        }

    async def list_dialogs(self, limit: int = 20) -> list[dict[str, Any]]:
        dialogs = await self._require().get_dialogs(limit=max(1, min(limit, 100)))
        return [
            {
                "id": d.id,
                "name": d.name,
                "unread_count": d.unread_count,
                "archived": bool(getattr(d, "archived", False)),
                "is_user": bool(d.is_user),
                "is_group": bool(d.is_group),
                "is_channel": bool(d.is_channel),
            }
            for d in dialogs
        ]

    async def search_entities(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        result = await self._require()(functions.contacts.SearchRequest(q=query, limit=max(1, min(limit, 50))))
        entities = (list(result.users) + list(result.chats))[:limit]
        return [
            {
                "id": getattr(e, "id", None),
                "username": getattr(e, "username", None),
                "title": getattr(e, "title", None),
                "first_name": getattr(e, "first_name", None),
                "last_name": getattr(e, "last_name", None),
            }
            for e in entities
        ]

    @staticmethod
    def _message_dict(m: Any) -> dict[str, Any]:
        return {
            "id": m.id,
            "date": m.date.isoformat() if getattr(m, "date", None) else None,
            "sender_id": getattr(m, "sender_id", None),
            "text": getattr(m, "message", "") or "",
            "out": bool(getattr(m, "out", False)),
            "media": type(m.media).__name__ if getattr(m, "media", None) else None,
        }

    async def recent_messages(self, peer: Any, limit: int = 20) -> list[dict[str, Any]]:
        messages = await self._require().get_messages(peer, limit=max(1, min(limit, 100)))
        return [self._message_dict(m) for m in messages]

    async def search_messages(self, peer: Any, query: str, limit: int = 20) -> list[dict[str, Any]]:
        messages = await self._require().get_messages(peer, limit=max(1, min(limit, 100)), search=query)
        return [self._message_dict(m) for m in messages]

    async def global_search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        result = await self._require()(
            functions.messages.SearchGlobalRequest(
                q=query,
                filter=types.InputMessagesFilterEmpty(),
                min_date=None,
                max_date=None,
                offset_rate=0,
                offset_peer=types.InputPeerEmpty(),
                offset_id=0,
                limit=max(1, min(limit, 50)),
            )
        )
        return [self._message_dict(m) for m in getattr(result, "messages", [])[:limit]]

    async def contacts(self) -> list[dict[str, Any]]:
        result = await self._require()(functions.contacts.GetContactsRequest(hash=0))
        return [
            {
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "phone": getattr(u, "phone", None),
            }
            for u in result.users
        ]

    async def get_entity_info(self, peer: Any) -> dict[str, Any]:
        e = await self._require().get_entity(peer)
        return {
            "id": getattr(e, "id", None),
            "username": getattr(e, "username", None),
            "title": getattr(e, "title", None),
            "first_name": getattr(e, "first_name", None),
            "last_name": getattr(e, "last_name", None),
            "bot": bool(getattr(e, "bot", False)),
            "verified": bool(getattr(e, "verified", False)),
            "premium": bool(getattr(e, "premium", False)),
        }

    async def send_message(self, peer: Any, text: str, reply_to: int | None = None) -> dict[str, Any]:
        m = await self._require().send_message(peer, text, reply_to=reply_to)
        return {"ok": True, "message_id": m.id, "peer": str(peer)}

    async def edit_message(self, peer: Any, message_id: int, text: str) -> dict[str, Any]:
        m = await self._require().edit_message(peer, message_id, text)
        return {"ok": True, "message_id": getattr(m, "id", message_id)}

    async def delete_messages(self, peer: Any, message_ids: list[int], revoke: bool = True) -> dict[str, Any]:
        result = await self._require().delete_messages(peer, message_ids, revoke=revoke)
        return {"ok": True, "deleted": message_ids, "updates": len(result or [])}

    async def forward_message(self, target: Any, source: Any, message_id: int) -> dict[str, Any]:
        result = await self._require().forward_messages(target, message_id, source)
        msg = result[0] if isinstance(result, list) else result
        return {"ok": True, "message_id": getattr(msg, "id", None)}

    async def pin(self, peer: Any, message_id: int, notify: bool = False) -> dict[str, Any]:
        await self._require().pin_message(peer, message_id, notify=notify)
        return {"ok": True}

    async def unpin(self, peer: Any, message_id: int | None = None) -> dict[str, Any]:
        await self._require().unpin_message(peer, message=message_id)
        return {"ok": True}

    async def mark_read(self, peer: Any) -> dict[str, Any]:
        await self._require().send_read_acknowledge(peer)
        return {"ok": True}

    async def archive(self, peer: Any, archived: bool = True) -> dict[str, Any]:
        await self._require().edit_folder(peer, 1 if archived else 0)
        return {"ok": True, "archived": archived}

    async def mute(self, peer: Any, minutes: int = 60) -> dict[str, Any]:
        client = self._require()
        entity = await client.get_input_entity(peer)
        until = datetime.now(UTC) + timedelta(minutes=max(1, min(minutes, 525600)))
        settings = types.InputPeerNotifySettings(mute_until=until)
        await client(functions.account.UpdateNotifySettingsRequest(peer=types.InputNotifyPeer(entity), settings=settings))
        return {"ok": True, "mute_until": until.isoformat()}

    async def unmute(self, peer: Any) -> dict[str, Any]:
        client = self._require()
        entity = await client.get_input_entity(peer)
        settings = types.InputPeerNotifySettings(mute_until=datetime.fromtimestamp(0, UTC))
        await client(functions.account.UpdateNotifySettingsRequest(peer=types.InputNotifyPeer(entity), settings=settings))
        return {"ok": True}

    async def block(self, peer: Any) -> dict[str, Any]:
        entity = await self._require().get_input_entity(peer)
        await self._require()(functions.contacts.BlockRequest(id=entity))
        return {"ok": True}

    async def unblock(self, peer: Any) -> dict[str, Any]:
        entity = await self._require().get_input_entity(peer)
        await self._require()(functions.contacts.UnblockRequest(id=entity))
        return {"ok": True}

    async def add_contact(self, peer: Any, first_name: str, last_name: str = "", phone: str = "") -> dict[str, Any]:
        client = self._require()
        entity = await client.get_input_entity(peer)
        result = await client(functions.contacts.AddContactRequest(
            id=entity, first_name=first_name, last_name=last_name, phone=phone,
            add_phone_privacy_exception=False,
        ))
        return {"ok": True, "updates": type(result).__name__}

    async def import_contact(self, phone: str, first_name: str, last_name: str = "") -> dict[str, Any]:
        contact = types.InputPhoneContact(client_id=1, phone=phone, first_name=first_name, last_name=last_name)
        result = await self._require()(functions.contacts.ImportContactsRequest([contact]))
        return {
            "ok": True,
            "imported_user_ids": [x.user_id for x in result.imported],
            "retry_contacts": result.retry_contacts,
        }

    async def delete_contact(self, peer: Any) -> dict[str, Any]:
        client = self._require()
        entity = await client.get_input_entity(peer)
        await client(functions.contacts.DeleteContactsRequest(id=[entity]))
        return {"ok": True}

    async def update_profile(self, first_name: str | None = None, last_name: str | None = None, about: str | None = None) -> dict[str, Any]:
        await self._require()(functions.account.UpdateProfileRequest(first_name=first_name, last_name=last_name, about=about))
        return {"ok": True}

    async def set_chat_wallpaper(self, peer: Any, wallpaper_id: int, access_hash: int, *, for_both: bool = False) -> dict[str, Any]:
        client = self._require()
        entity = await client.get_input_entity(peer)
        request_cls = getattr(functions.messages, "SetChatWallPaperRequest", None)
        if request_cls is None:
            raise MTProtoUnavailable("Installed Telethon schema does not expose messages.setChatWallPaper")
        wallpaper = types.InputWallPaper(id=wallpaper_id, access_hash=access_hash)
        try:
            result = await client(request_cls(peer=entity, wallpaper=wallpaper, settings=types.WallPaperSettings(), for_both=for_both))
        except TypeError as exc:
            raise MTProtoUnavailable("Telethon wallpaper method signature differs from current Telegram layer") from exc
        return {"ok": True, "result": type(result).__name__}
