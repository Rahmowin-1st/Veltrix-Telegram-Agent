from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import Settings


class Base(DeclarativeBase):
    pass


class ChatMemory(Base):
    __tablename__ = "chat_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ChatPreference(Base):
    __tablename__ = "chat_preference"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(settings.db_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def add_memory(self, chat_id: int, role: str, content: str) -> None:
        if not await self.memory_enabled(chat_id):
            return
        async with self.sessions() as session:
            session.add(ChatMemory(chat_id=chat_id, role=role, content=content))
            await session.commit()

    async def get_history(self, chat_id: int, limit: int = 30) -> list[dict[str, str]]:
        if not await self.memory_enabled(chat_id):
            return []
        async with self.sessions() as session:
            q = (
                select(ChatMemory)
                .where(ChatMemory.chat_id == chat_id)
                .order_by(ChatMemory.id.desc())
                .limit(limit)
            )
            rows = list((await session.scalars(q)).all())
        rows.reverse()
        return [{"role": r.role, "content": r.content} for r in rows]

    async def clear_history(self, chat_id: int) -> int:
        from sqlalchemy import delete

        async with self.sessions() as session:
            result = await session.execute(delete(ChatMemory).where(ChatMemory.chat_id == chat_id))
            await session.commit()
            return result.rowcount or 0

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
