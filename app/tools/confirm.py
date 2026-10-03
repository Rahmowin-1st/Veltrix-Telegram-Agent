from __future__ import annotations

import secrets
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PendingAction:
    token: str
    chat_id: int
    action: str
    arguments: dict[str, Any]
    expires_at: float
    request_id: int


class ConfirmationManager:
    def __init__(self, ttl_seconds: int = 300, *, max_per_chat: int = 5, max_pending: int = 100):
        self.ttl_seconds = ttl_seconds
        self.max_per_chat = max_per_chat
        self.max_pending = max_pending
        self._pending: dict[str, PendingAction] = {}
        self._request_id = 0

    def create(self, chat_id: int, action: str, arguments: dict[str, Any]) -> PendingAction:
        self._prune()
        self._request_id += 1
        existing = self.for_chat(chat_id)
        for item in existing:
            if item.action == action and item.arguments == arguments:
                item.request_id = self._request_id
                return item  # Do not extend its expiry or create multiple identical previews.
        if len(existing) >= self.max_per_chat or len(self._pending) >= self.max_pending:
            raise RuntimeError(
                "Pending confirmation limit reached; cancel or finish earlier previews."
            )
        token = secrets.token_urlsafe(6)
        item = PendingAction(
            token,
            chat_id,
            action,
            deepcopy(arguments),
            time.time() + self.ttl_seconds,
            self._request_id,
        )
        self._pending[token] = item
        return item

    def consume(self, chat_id: int, token: str) -> PendingAction | None:
        self._prune()
        item = self._pending.get(token)
        if not item or item.chat_id != chat_id:
            return None
        self._pending.pop(token, None)
        return item

    def cancel_chat(self, chat_id: int) -> int:
        self._prune()
        tokens = [t for t, item in self._pending.items() if item.chat_id == chat_id]
        for token in tokens:
            self._pending.pop(token, None)
        return len(tokens)

    def for_chat(self, chat_id: int) -> list[PendingAction]:
        self._prune()
        return [item for item in self._pending.values() if item.chat_id == chat_id]

    def _prune(self) -> None:
        now = time.time()
        for token, item in list(self._pending.items()):
            if item.expires_at <= now:
                self._pending.pop(token, None)
