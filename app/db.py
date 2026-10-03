from __future__ import annotations

import time

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import Settings
from app.security.redaction import redact_text


class Base(DeclarativeBase):
    pass


class ChatMemory(Base):
    __tablename__ = "chat_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ChatPreference(Base):
    __tablename__ = "chat_preference"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UpdateReceipt(Base):
    """At-most-once dispatch marker. Contains no Telegram message or credential data."""

    __tablename__ = "update_receipt"
    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    claimed_at: Mapped[int] = mapped_column(BigInteger, index=True)


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(settings.db_url, pool_pre_ping=True)
        self.backend = (
            "postgres" if self.engine.url.get_backend_name() == "postgresql" else "sqlite"
        )
        self.memory_retention_messages = settings.memory_retention_messages
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def add_memory(self, chat_id: int, role: str, content: str) -> None:
        if not await self.memory_enabled(chat_id):
            return
        safe_content = redact_text(content)[:6000]
        async with self.sessions() as session:
            session.add(ChatMemory(chat_id=chat_id, role=role, content=safe_content))
            await session.flush()
            keep = (
                select(ChatMemory.id)
                .where(ChatMemory.chat_id == chat_id)
                .order_by(ChatMemory.id.desc())
                .limit(self.memory_retention_messages)
            )
            await session.execute(
                delete(ChatMemory).where(ChatMemory.chat_id == chat_id, ChatMemory.id.not_in(keep))
            )
            await session.commit()

    async def get_history(self, chat_id: int, limit: int = 30) -> list[dict[str, str]]:
        if not await self.memory_enabled(chat_id):
            return []
        async with self.sessions() as session:
            q = (
                select(ChatMemory)
                .where(ChatMemory.chat_id == chat_id)
                .order_by(ChatMemory.id.desc())
                .limit(max(1, min(int(limit), 100)))
            )
            rows = list((await session.scalars(q)).all())
        rows.reverse()
        return [{"role": r.role, "content": r.content} for r in rows]

    async def clear_history(self, chat_id: int) -> int:
        async with self.sessions() as session:
            result = await session.execute(delete(ChatMemory).where(ChatMemory.chat_id == chat_id))
            await session.commit()
            return result.rowcount or 0

    async def claim_update(self, bot_id: int, update_id: int) -> bool:
        """Commit BEFORE dispatch, so an uncertain mutation is never automatically replayed.

        A crashed/failed claimed update is deliberately not replayed. This is not an
        exactly-once or durable-job guarantee; receipts survive only as long as the DB.
        """
        async with self.sessions() as session:
            await session.execute(
                delete(UpdateReceipt).where(UpdateReceipt.claimed_at < int(time.time()) - 7 * 86400)
            )
            session.add(
                UpdateReceipt(bot_id=bot_id, update_id=update_id, claimed_at=int(time.time()))
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if await session.get(UpdateReceipt, (bot_id, update_id)) is not None:
                    return False
                raise
        return True

    async def memory_enabled(self, chat_id: int) -> bool:
        async with self.sessions() as session:
            row = await session.get(ChatPreference, chat_id)
            return True if row is None else bool(row.memory_enabled)

    async def set_memory_enabled(self, chat_id: int, enabled: bool) -> None:
        async with self.sessions() as session:
            row = await session.get(ChatPreference, chat_id)
            if row is None:
                row = ChatPreference(chat_id=chat_id, memory_enabled=enabled)
                session.add(row)
            else:
                row.memory_enabled = enabled
            await session.commit()
