import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from telegram import Update, User
from telegram.ext import Application, ExtBot, TypeHandler

from app.ai.gemini_client import GeminiError
from app.ai.schemas import TOOL_DECLARATIONS
from app.api import router
from app.config import Settings
from app.db import Database
from app.telegram.handlers import WebhookBusy
from app.tools.confirm import ConfirmationManager
from app.tools.telegram_write import TelegramWriteTools
from tests.test_account_commands import runtime, update
from tests.test_natural_agent import make_agent, mt_client, response

CHAT_TOOLS = {
    "assistant_status",
    "assistant_capabilities",
    "memory_status",
    "set_memory",
    "forget_conversation",
    "cancel_pending_actions",
}


def test_all_account_actions_and_chat_controls_are_ai_callable():
    names = {d["name"] for d in TOOL_DECLARATIONS}
    assert set(TelegramWriteTools.ACTION_METHODS) | CHAT_TOOLS <= names
    assert not names & {"confirm", "confirm_action", "execute_confirmed"}


@pytest.mark.asyncio
async def test_ai_memory_tools_cannot_choose_another_chat():
    agent, _ = make_agent(mt_client(), [])
    agent.db.set_memory_enabled = AsyncMock()
    result = await agent._execute_tool(
        "set_memory",
        {"enabled": False, "chat_id": 99, "owner_chat_id": 99},
        42,
        allow_chat_tools=True,
    )
    assert result["ok"]
    agent.db.set_memory_enabled.assert_awaited_once_with(42, False)
    denied = await agent._execute_tool("set_memory", {"enabled": True}, 99)
    assert denied["error"] == "ChatControlAccessDenied"


@pytest.mark.asyncio
async def test_ai_can_cancel_only_current_chat_pending_actions():
    agent, _ = make_agent(mt_client(), [])
    manager = agent.writes.confirmations
    manager.create(42, "block_user", {"peer": "@one"})
    other = manager.create(99, "block_user", {"peer": "@other"})
    result = await agent._execute_tool("cancel_pending_actions", {}, 42, allow_chat_tools=True)
    assert result == {"ok": True, "cancelled": 1}
    assert manager.consume(99, other.token)


@pytest.mark.asyncio
async def test_forget_does_not_save_the_deleted_turn_again():
    agent, snapshots = make_agent(
        mt_client(),
        [
            response({"functionCall": {"name": "forget_conversation", "args": {}}}),
            response({"text": "Suhbat xotirasi tozalandi."}),
        ],
    )
    agent.db.clear_history = AsyncMock(return_value=3)
    await agent.chat(
        chat_id=42, text="Suhbatimizni unut", allow_account_tools=True, allow_chat_tools=True
    )
    agent.db.clear_history.assert_awaited_once_with(42)
    assert agent.db.add_memory.await_count == 1  # The pre-clear user entry only.
    assert "forget_conversation" in {d["name"] for d in snapshots[0]["function_declarations"]}


@pytest.mark.asyncio
async def test_group_does_not_receive_private_chat_controls():
    agent, snapshots = make_agent(mt_client(), [response({"text": "Salom"})])
    await agent.chat(chat_id=-42, text="Salom", allow_account_tools=False, allow_chat_tools=False)
    assert not CHAT_TOOLS & {d["name"] for d in snapshots[0]["function_declarations"]}
    obj, _ = runtime()
    await obj.handle_update(update("Salom", chat_type="group"), None)
    assert obj.agent.chat.call_args.kwargs["allow_chat_tools"] is False


@pytest.mark.asyncio
async def test_status_tool_reports_probe_not_configured_as_verified():
    agent, _ = make_agent(mt_client(), [])
    agent.db.backend = "sqlite"
    agent.db.memory_enabled = AsyncMock(return_value=True)
    agent.gemini.readiness_probe_passed = None
    result = await agent._execute_tool("assistant_status", {}, 42, allow_chat_tools=True)
    assert result["ai_verified"] is None
    assert result["account_access"] is False
    assert not {"phone", "telegram_session", "owner_telegram_id"} & set(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/actions", "/tools", "/do", "/confirm", "/unknown"])
async def test_normal_help_never_requires_json_or_tokens(text):
    obj, _ = runtime()
    u = update(text)
    await obj.handle_update(u, None)
    answer = u.effective_message.reply_text.call_args.args[0]
    assert not any(s in answer for s in ("/do", "/confirm", "TOKEN", "{JSON}", "ACTION"))


@pytest.mark.asyncio
async def test_reply_context_is_data_and_not_an_mtproto_id():
    obj, _ = runtime()
    u = update("Buni Adminga yubor")
    u.effective_message.reply_to_message = SimpleNamespace(
        message_id=12,
        text="Salom!",
        caption=None,
        document=None,
        audio=None,
        voice=None,
        video=None,
        photo=None,
        sticker=None,
    )
    await obj.handle_update(u, None)
    prompt = obj.agent.chat.call_args.kwargs["text"]
    assert "Salom!" in prompt and "UNTRUSTED" in prompt
    assert "not an MTProto" in prompt


@pytest.mark.asyncio
async def test_same_chat_updates_are_serial_and_lock_entries_are_released():
    obj, _ = runtime()
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def chat(**kwargs):
        order.append(kwargs["text"])
        if kwargs["text"] == "one":
            entered.set()
            await release.wait()
        return "ok"

    obj.agent.chat = chat
    first = asyncio.create_task(obj.handle_update(update("one"), None))
    await entered.wait()
    second = asyncio.create_task(obj.handle_update(update("two"), None))
    await asyncio.sleep(0)
    assert order == ["one"]
    release.set()
    await asyncio.gather(first, second)
    assert order == ["one", "two"]
    assert not obj._chat_locks


@pytest.mark.asyncio
async def test_duplicate_delivery_is_claimed_before_ai_or_mutations():
    obj, _ = runtime()
    obj.db.claim_update = AsyncMock(side_effect=[True, False])
    u = update("Savedga Salom yubor")
    u.update_id = 10
    await obj.handle_update(u, None)
    await obj.handle_update(u, None)
    obj.agent.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_enqueues_without_waiting_for_ai_and_rejects_overload(monkeypatch):
    obj, _ = runtime()
    queued = SimpleNamespace(update_id=1)
    monkeypatch.setattr("app.telegram.handlers.Update.de_json", lambda *a: queued)
    obj.application = SimpleNamespace(
        running=True,
        bot=object(),
        update_queue=asyncio.Queue(maxsize=1),
        process_update=AsyncMock(),
    )
    await obj.process_webhook({"update_id": 1})
    await obj.process_webhook({"update_id": 1})
    assert obj.application.update_queue.qsize() == 1
    obj.application.process_update.assert_not_awaited()
    queued.update_id = 2
    with pytest.raises(WebhookBusy):
        await obj.process_webhook({"update_id": 2})
    assert 2 not in obj._queued_updates


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,status",
    [(b"[1]", 400), (b"{", 400), (b'{"update_id":true}', 400), (b"x" * 262145, 413)],
    ids=["array", "invalid-json", "boolean-id", "oversized"],
)
async def test_webhook_malformed_and_oversized_payloads_are_rejected(payload, status):
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = SimpleNamespace(
        settings=Settings(_env_file=None),
        telegram=SimpleNamespace(valid_webhook_secret=lambda _: True, process_webhook=AsyncMock()),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post("/telegram/webhook", content=payload)
    assert r.status_code == status
    app.state.runtime.telegram.process_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_size_is_checked_before_download():
    obj, _ = runtime()
    u = update("Qisqartir")
    u.effective_message.document = SimpleNamespace(
        file_size=100 * 1024 * 1024, mime_type="text/plain", get_file=AsyncMock()
    )
    note = await obj._understand_media(u.effective_message)
    assert "limit" in note
    u.effective_message.document.get_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_queue_dispatches_with_real_application_offline(monkeypatch):
    obj, _ = runtime()
    bot = ExtBot("1:offline-only")
    bot._bot_user = User(1, "test", True)
    monkeypatch.setattr(ExtBot, "initialize", AsyncMock())
    monkeypatch.setattr(ExtBot, "shutdown", AsyncMock())
    app = Application.builder().bot(bot).updater(None).update_queue(asyncio.Queue(10)).build()
    obj.application = app
    obj.db.claim_update = AsyncMock(return_value=True)
    obj._handle_update = AsyncMock()
    app.add_handler(TypeHandler(Update, obj.handle_update))
    await app.initialize()
    await app.start()
    try:
        payload = {
            "update_id": 17,
            "message": {
                "message_id": 1,
                "date": 1,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "first_name": "owner", "is_bot": False},
                "text": "Salom",
            },
        }
        await obj.process_webhook(payload)
        await asyncio.wait_for(app.update_queue.join(), 1)
        obj._handle_update.assert_awaited_once()
        await obj.process_webhook(payload)
        assert app.update_queue.empty()
    finally:
        await app.stop()
        await app.shutdown()
        for request in bot._request:
            await request.shutdown()


@pytest.mark.asyncio
async def test_edited_message_cannot_reexecute_account_actions():
    obj, _ = runtime()
    u = update("Savedga Salom yubor")
    u.edited_message = u.effective_message
    await obj.handle_update(u, None)
    obj.agent.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_preview_still_has_buttons_if_ai_fails_after_tool():
    obj, _ = runtime()

    async def failing_chat(**kwargs):
        obj.confirmations.create(42, "delete_messages", {"peer": "me", "message_ids": [1]})
        raise GeminiError("provider unavailable", status_code=429)

    obj.agent.chat = failing_chat
    u = update("Bu xabarni o‘chir")
    await obj.handle_update(u, None)
    markup = u.effective_message.reply_text.call_args.kwargs["reply_markup"]
    assert "Xabarlarni o‘chirish" in markup.inline_keyboard[0][0].text
    assert markup.inline_keyboard[0][0].callback_data.startswith("confirm:")


@pytest.mark.asyncio
async def test_repeated_preview_reuses_token_but_redisplays_button():
    obj, _ = runtime()
    pending = obj.confirmations.create(42, "block_user", {"peer": "@admin"})
    expiry = pending.expires_at

    async def chat(**kwargs):
        obj.confirmations.create(42, "block_user", {"peer": "@admin"})
        return "Bloklashni tasdiqlaysizmi?"

    obj.agent.chat = chat
    u = update("Adminni blokla")
    await obj.handle_update(u, None)
    markup = u.effective_message.reply_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"confirm:{pending.token}"
    assert pending.expires_at == expiry
    assert len(obj.confirmations.for_chat(42)) == 1


@pytest.mark.asyncio
async def test_turn_deadline_does_not_retry_a_write():
    agent, _ = make_agent(mt_client(), [])
    agent.settings.ai_turn_timeout_seconds = 0.01
    started = asyncio.Event()

    async def stuck(**kwargs):
        started.set()
        await asyncio.Event().wait()

    agent.gemini.agent_turn = AsyncMock(side_effect=stuck)
    text = await agent.chat(chat_id=42, text="Salom", allow_account_tools=True)
    assert started.is_set() and "takrorlamayman" in text
    agent.gemini.agent_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_tool_budget_prevents_additional_writes():
    agent, snapshots = make_agent(
        mt_client(),
        [
            response(
                {"functionCall": {"name": "mark_read", "args": {"peer": "me"}}},
                {"functionCall": {"name": "archive_chat", "args": {"peer": "me"}}},
            ),
            response({"text": "Birinchi amal bajarildi; ikkinchisi bajarilmadi."}),
        ],
    )
    agent.settings.max_tool_calls_per_turn = 1
    agent.writes.mt.mark_read = AsyncMock(return_value={"ok": True})
    agent.writes.mt.archive = AsyncMock()
    await agent.chat(chat_id=42, text="O‘qi va arxivla", allow_account_tools=True)
    agent.writes.mt.mark_read.assert_awaited_once()
    agent.writes.mt.archive.assert_not_awaited()
    parts = snapshots[1]["contents"][-1]["parts"]
    assert parts[1]["functionResponse"]["response"]["result"]["error"] == "TurnToolLimit"


@pytest.mark.asyncio
async def test_completed_write_receipt_survives_following_provider_failure():
    agent, _ = make_agent(
        mt_client(),
        [
            response(
                {"functionCall": {"name": "send_message", "args": {"peer": "me", "text": "Salom"}}}
            ),
        ],
    )
    agent.writes.mt.send_message = AsyncMock(return_value={"ok": True, "message_id": 88})
    with pytest.raises(IndexError):  # Synthetic provider fails on its second turn.
        await agent.chat(chat_id=42, text="Savedga Salom yubor", allow_account_tools=True)
    agent.writes.mt.send_message.assert_awaited_once()
    receipt = agent.db.add_memory.call_args.args[2]
    assert "Internal action receipt" in receipt and '"message_id": 88' in receipt


@pytest.mark.asyncio
async def test_confirmed_action_is_recorded_for_subsequent_ai_context():
    obj, _ = runtime()
    obj.agent.record_action = AsyncMock()
    obj.writes.execute_confirmed = AsyncMock(return_value={"ok": True})
    pending = obj.confirmations.create(42, "leave_channel", {"peer": "@one"})
    await obj.handle_update(update(f"/confirm {pending.token}"), None)
    obj.agent.record_action.assert_awaited_once_with(
        42, "leave_channel", {"peer": "@one"}, {"ok": True}
    )


def test_pending_previews_are_deduplicated_bounded_and_not_mutable():
    manager = ConfirmationManager(max_per_chat=2, max_pending=3)
    args = {"peer": "me", "message_ids": [1]}
    first = manager.create(42, "delete_messages", args)
    assert manager.create(42, "delete_messages", args) is first
    args["message_ids"].append(2)
    assert first.arguments["message_ids"] == [1]
    manager.create(42, "block_user", {"peer": "@one"})
    with pytest.raises(RuntimeError, match="confirmation limit"):
        manager.create(42, "block_user", {"peer": "@two"})
    manager.create(99, "block_user", {"peer": "@one"})
    with pytest.raises(RuntimeError, match="confirmation limit"):
        manager.create(100, "block_user", {"peer": "@one"})


@pytest.mark.asyncio
async def test_receipts_persist_and_memory_is_bounded(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'qa.db'}",
        memory_retention_messages=20,
    )
    db = Database(settings)
    await db.init()
    assert await db.claim_update(1, 10)
    assert not await db.claim_update(1, 10)
    assert await db.claim_update(2, 10)  # Independent bots.
    for n in range(25):
        await db.add_memory(42, "user", str(n))
    history = await db.get_history(42, 100)
    assert [item["content"] for item in history] == [str(n) for n in range(5, 25)]
    await db.engine.dispose()
    reopened = Database(settings)
    await reopened.init()
    assert not await reopened.claim_update(1, 10)
    await reopened.engine.dispose()
