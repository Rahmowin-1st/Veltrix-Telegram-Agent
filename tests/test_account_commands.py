from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.agent import Agent
from app.config import Settings
from app.telegram.handlers import TelegramRuntime
from app.tools.confirm import ConfirmationManager
from app.tools.telegram_write import TelegramWriteTools


def runtime(owner=42):
    settings = Settings(_env_file=None, owner_telegram_id=owner)
    mt = SimpleNamespace(
        ready=True, me=AsyncMock(return_value={"id": owner, "phone": "hidden"}),
        list_dialogs=AsyncMock(return_value=[]), recent_messages=AsyncMock(return_value=[]),
        contacts=AsyncMock(return_value=[]), global_search=AsyncMock(return_value=[]),
    )
    confirmations = ConfirmationManager()
    writes = TelegramWriteTools(mt, settings, confirmations)
    obj = TelegramRuntime(settings, SimpleNamespace(backend="sqlite"), SimpleNamespace(ready=False),
                          SimpleNamespace(chat=AsyncMock(return_value="ok")), confirmations, writes)
    return obj, mt


def update(text, user_id=42, chat_type="private"):
    msg = SimpleNamespace(text=text, caption=None, chat_id=42, chat=SimpleNamespace(type=chat_type),
                          reply_text=AsyncMock(), voice=None, audio=None, photo=None,
                          document=None, video=None)
    return SimpleNamespace(effective_message=msg, effective_user=SimpleNamespace(id=user_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,chat_type", [(99, "private"), (42, "group"), (42, "supergroup")])
async def test_account_access_denied(user_id, chat_type):
    obj, mt = runtime()
    u = update("/account", user_id, chat_type)
    await obj.handle_update(u, None)
    mt.me.assert_not_awaited()
    assert "only to the owner" in u.effective_message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_owner_account_and_real_status():
    obj, mt = runtime()
    u = update("/account")
    await obj.handle_update(u, None)
    mt.me.assert_awaited_once()
    assert '"phone": "hidden"' in u.effective_message.reply_text.call_args.args[0]
    u = update("/status")
    await obj.handle_update(u, None)
    result = u.effective_message.reply_text.call_args.args[0]
    assert "mtproto_running: True" in result
    assert "owner_matches_account: True" in result


@pytest.mark.asyncio
async def test_reads_are_bounded_and_numeric_peer():
    obj, mt = runtime()
    await obj.handle_update(update("/chats 9999"), None)
    mt.list_dialogs.assert_awaited_once_with(100)
    await obj.handle_update(update("/messages -100123 5"), None)
    mt.recent_messages.assert_awaited_once_with(-100123, 5)
    await obj.handle_update(update("/search my query"), None)
    mt.global_search.assert_awaited_once_with("my query", 20)


@pytest.mark.asyncio
async def test_direct_write_requires_confirmation_and_is_single_use():
    obj, mt = runtime()

    async def send_message(peer, text, reply_to=None):
        return {"ok": True}

    mt.send_message = send_message
    obj.writes.execute_confirmed = AsyncMock(return_value={"ok": True})
    await obj.handle_update(update('/do send_message {"peer":"me","text":"Hello"}'), None)
    obj.writes.execute_confirmed.assert_not_awaited()
    token = next(iter(obj.confirmations._pending))
    await obj.handle_update(update(f"/confirm {token}", user_id=99), None)
    obj.writes.execute_confirmed.assert_not_awaited()
    await obj.handle_update(update(f"/confirm {token}"), None)
    obj.writes.execute_confirmed.assert_awaited_once_with("send_message", {"peer": "me", "text": "Hello"})
    await obj.handle_update(update(f"/confirm {token}"), None)
    assert obj.writes.execute_confirmed.await_count == 1


@pytest.mark.asyncio
async def test_invalid_action_cannot_create_pending():
    obj, _ = runtime()
    for text in ['/do unknown {}', '/do send_message []', '/do update_profile {bad']:
        await obj.handle_update(update(text), None)
    assert not obj.confirmations._pending


@pytest.mark.asyncio
async def test_ai_dispatch_refuses_hidden_account_tools():
    agent = Agent.__new__(Agent)
    tool = AsyncMock(return_value={"id": 42})
    agent.tool_map = {"account_info": tool}
    result = await agent._execute_tool("account_info", {}, 99, allow_account_tools=False)
    assert result == {"error": "AccountAccessDenied"}
    tool.assert_not_awaited()
    assert await agent._execute_tool("account_info", {}, 42, allow_account_tools=True) == {"id": 42}


@pytest.mark.asyncio
async def test_group_ai_has_no_account_authority():
    obj, _ = runtime()
    await obj.handle_update(update("hello", chat_type="group"), None)
    assert obj.agent.chat.call_args.kwargs["allow_account_tools"] is False


@pytest.mark.asyncio
async def test_account_writes_rate_limited():
    obj, mt = runtime()
    obj.settings.write_rate_per_minute = 1
    mt.mark_read = AsyncMock(return_value={"ok": True})
    await obj.writes.execute_confirmed("mark_read", {"peer": "me"})
    with pytest.raises(RuntimeError, match="rate limit"):
        await obj.writes.execute_confirmed("mark_read", {"peer": "me"})
    mt.mark_read.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnected_never_attempts_read():
    obj, mt = runtime()
    mt.ready = False
    u = update("/account")
    await obj.handle_update(u, None)
    mt.me.assert_not_awaited()
    assert "disconnected" in u.effective_message.reply_text.call_args.args[0]
