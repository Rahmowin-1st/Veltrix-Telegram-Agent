from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    port: int = 10000
    owner_telegram_id: int | None = None

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    gemini_search_model: str = "gemini-3.8-flash"

    telegram_bot_token: str | None = None
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session: str | None = None

    database_url: str | None = None

    auto_reply_business: bool = False
    require_confirmation: bool = True
    max_history_messages: int = Field(default=30, ge=5, le=100)
    max_agent_steps: int = Field(default=8, ge=1, le=16)
    write_rate_per_minute: int = Field(default=20, ge=1, le=120)
    download_max_mb: int = Field(default=25, ge=1, le=100)
    allowed_fetch_schemes: str = "https"

    @field_validator("gemini_api_key", "telegram_bot_token", "telegram_api_hash", "telegram_session", mode="before")
    @classmethod
    def empty_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_bot(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def has_mtproto(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash and self.telegram_session)

    @property
    def db_url(self) -> str:
        raw = self.database_url or "sqlite+aiosqlite:///./veltrix.db"
        if raw.startswith("postgres://"):
            raw = raw.replace("postgres://", "postgresql+asyncpg://", 1)
        elif raw.startswith("postgresql://") and "+asyncpg" not in raw:
            raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
        return raw

    def setup_status(self) -> dict[str, object]:
        missing = []
        if not self.has_gemini:
            missing.append("GEMINI_API_KEY")
        if not self.has_bot:
            missing.append("TELEGRAM_BOT_TOKEN")
        mtproto_missing = []
        if not self.telegram_api_id:
            mtproto_missing.append("TELEGRAM_API_ID")
        if not self.telegram_api_hash:
            mtproto_missing.append("TELEGRAM_API_HASH")
        if not self.telegram_session:
            mtproto_missing.append("TELEGRAM_SESSION")
        return {
            "environment": self.environment,
            "gemini_ready": self.has_gemini,
            "bot_ready": self.has_bot,
            "mtproto_ready": self.has_mtproto,
            "missing_required_for_core": missing,
            "missing_for_full_account_control": mtproto_missing,
            "model": self.gemini_model,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
