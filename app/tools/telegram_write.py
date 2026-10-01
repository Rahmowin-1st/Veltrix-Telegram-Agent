from __future__ import annotations

import time
from collections import deque
from typing import Any

from app.config import Settings
from app.security.policy import evaluate_action
from app.telegram.mtproto_client import MTProtoClient
from app.tools.confirm import ConfirmationManager


class TelegramWriteTools:
    ACTION_METHODS = {
        "send_message": "send_message", "edit_message": "edit_message",
        "delete_messages": "delete_messages", "forward_message": "forward_message",
        "pin_message": "pin", "unpin_message": "unpin", "mark_read": "mark_read",
        "archive_chat": "archive", "mute_chat": "mute", "unmute_chat": "unmute",
        "block_user": "block", "unblock_user": "unblock", "add_contact": "add_contact",
        "import_contact": "import_contact", "delete_contact": "delete_contact",
        "update_profile": "update_profile", "set_chat_wallpaper": "set_chat_wallpaper",
    }
    def __init__(self, mt: MTProtoClient, settings: Settings, confirmations: ConfirmationManager):
        self.mt = mt
        self.settings = settings
        self.confirmations = confirmations
        self._write_times: deque[float] = deque()

    async def _run(self, *, owner_chat_id: int, action: str, args: dict[str, Any], explicit_current_request: bool = True):
        decision = evaluate_action(
            action,
            explicit_current_request=explicit_current_request,
            require_confirmation=self.settings.require_confirmation,
        )
        if decision.requires_confirmation:
            pending = self.confirmations.create(owner_chat_id, action, args)
            return {
                "ok": False,
                "confirmation_required": True,
                "token": pending.token,
                "action": action,
                "summary": args,
            }
        return await self.execute_confirmed(action, args)

    async def execute_confirmed(self, action: str, args: dict[str, Any]):
        if action not in self.ACTION_METHODS:
            raise ValueError(f"Unknown confirmed action: {action}")
        now = time.monotonic()
        while self._write_times and self._write_times[0] <= now - 60:
            self._write_times.popleft()
        if len(self._write_times) >= self.settings.write_rate_per_minute:
            raise RuntimeError("Account write rate limit reached; wait one minute before retrying.")
        self._write_times.append(now)
        return await getattr(self.mt, self.ACTION_METHODS[action])(**args)

    async def send_message(self, owner_chat_id: int, peer: str, text: str, reply_to: int | None = None, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="send_message", args={"peer": peer, "text": text, "reply_to": reply_to})

    async def edit_message(self, owner_chat_id: int, peer: str, message_id: int, text: str, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="edit_message", args={"peer": peer, "message_id": message_id, "text": text})

    async def delete_messages(self, owner_chat_id: int, peer: str, message_ids: list[int], revoke: bool = True, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="delete_messages", args={"peer": peer, "message_ids": message_ids, "revoke": revoke})

    async def forward_message(self, owner_chat_id: int, target: str, source: str, message_id: int, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="forward_message", args={"target": target, "source": source, "message_id": message_id})

    async def pin_message(self, owner_chat_id: int, peer: str, message_id: int, notify: bool = False, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="pin_message", args={"peer": peer, "message_id": message_id, "notify": notify})

    async def unpin_message(self, owner_chat_id: int, peer: str, message_id: int | None = None, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="unpin_message", args={"peer": peer, "message_id": message_id})

    async def mark_read(self, owner_chat_id: int, peer: str, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="mark_read", args={"peer": peer})

    async def archive_chat(self, owner_chat_id: int, peer: str, archived: bool = True, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="archive_chat", args={"peer": peer, "archived": archived})

    async def mute_chat(self, owner_chat_id: int, peer: str, minutes: int = 60, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="mute_chat", args={"peer": peer, "minutes": minutes})

    async def unmute_chat(self, owner_chat_id: int, peer: str, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="unmute_chat", args={"peer": peer})

    async def block_user(self, owner_chat_id: int, peer: str, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="block_user", args={"peer": peer})

    async def unblock_user(self, owner_chat_id: int, peer: str, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="unblock_user", args={"peer": peer})

    async def add_contact(self, owner_chat_id: int, peer: str, first_name: str, last_name: str = "", phone: str = "", **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="add_contact", args={"peer": peer, "first_name": first_name, "last_name": last_name, "phone": phone})

    async def import_contact(self, owner_chat_id: int, phone: str, first_name: str, last_name: str = "", **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="import_contact", args={"phone": phone, "first_name": first_name, "last_name": last_name})

    async def delete_contact(self, owner_chat_id: int, peer: str, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="delete_contact", args={"peer": peer})

    async def update_profile(self, owner_chat_id: int, first_name: str | None = None, last_name: str | None = None, about: str | None = None, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="update_profile", args={"first_name": first_name, "last_name": last_name, "about": about})

    async def set_chat_wallpaper(self, owner_chat_id: int, peer: str, wallpaper_id: int, access_hash: int, for_both: bool = False, **_: Any):
        return await self._run(owner_chat_id=owner_chat_id, action="set_chat_wallpaper", args={"peer": peer, "wallpaper_id": wallpaper_id, "access_hash": access_hash, "for_both": for_both})
