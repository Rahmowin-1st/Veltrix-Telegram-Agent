from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PendingAction:
    token: str
    chat_id: int
    action: str
    arguments: dict[str, Any]
    expires_at: float


class ConfirmationManager:
    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, PendingAction] = {}

    def create(self, chat_id: int, action: str, arguments: dict[str, Any]) -> PendingAction:
        self._prune()
        token = secrets.token_urlsafe(6)
        item = PendingAction(token, chat_id, action, arguments, time.time() + self.ttl_seconds)
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
