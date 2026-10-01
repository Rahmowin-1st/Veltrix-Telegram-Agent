from app.config import Settings


def test_missing_secrets_do_not_crash_settings():
    settings = Settings(_env_file=None)
    status = settings.setup_status()
    assert status["gemini_ready"] is False
    assert status["bot_ready"] is False
    assert status["mtproto_ready"] is False


def test_postgres_url_is_asyncpg():
    settings = Settings(_env_file=None, database_url="postgresql://u:p@host/db")
    assert settings.db_url.startswith("postgresql+asyncpg://")
