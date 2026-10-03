import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from telethon import functions, types

from app.ai.agent import Agent
from app.ai.gemini_client import GeminiClient, GeminiError
from app.ai.schemas import TOOL_DECLARATIONS
from app.config import Settings
from app.security.policy import HIGH_IMPACT_ACTIONS, evaluate_action
from app.telegram.mtproto_client import MTProtoClient, MTProtoUnavailable
from app.tools.confirm import ConfirmationManager
from app.tools.telegram_read import TelegramReadTools
from app.tools.telegram_write import TelegramWriteTools
from tests.test_account_commands import runtime, update


async def stream(items):
    for item in items:
        yield item


def mt_client(**methods):
    mt = MTProtoClient(Settings(_env_file=None))
    mt.client = SimpleNamespace(is_connected=lambda: True, **methods)
    return mt


def response(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}]}


def make_agent(mt, replies):
    snapshots = []

    async def turn(**kwargs):
        snapshots.append(copy.deepcopy(kwargs))
        return replies.pop(0)

    gemini = SimpleNamespace(
        ready=True,
        agent_turn=turn,
        model_content_from_response=GeminiClient.model_content_from_response,
        extract_function_calls=GeminiClient.extract_function_calls,
        extract_text=GeminiClient.extract_text,
    )
    settings = Settings(_env_file=None)
    db = SimpleNamespace(get_history=AsyncMock(return_value=[]), add_memory=AsyncMock())
    writes = TelegramWriteTools(mt, settings, ConfirmationManager())
    return Agent(settings, db, gemini, TelegramReadTools(mt), writes), snapshots


def test_all_declarations_have_executable_handlers():
    agent, _ = make_agent(mt_client(), [])
    names = {d["name"] for d in TOOL_DECLARATIONS}
    assert names == set(agent.tool_map) | {"react_to_user_message"}
    assert len(names) >= 50


@pytest.mark.asyncio
async def test_local_name_ambiguity_and_exact_match():
    contact = types.User(id=1, first_name="Admin")
    other = types.User(id=2, first_name="Admin")

    class Client:
        def is_connected(self):
            return True

        async def __call__(self, request):
            return SimpleNamespace(users=[contact])

        def iter_dialogs(self, **kwargs):
            return stream([SimpleNamespace(entity=other, name="Admin")])

    mt = mt_client()
    mt.client = Client()
    result = await mt.resolve_chat("admin")
    assert result["ambiguous"] and result["match_count"] == 2
    assert {row["id"] for row in result["matches"]} == {"1", "2"}


@pytest.mark.asyncio
async def test_music_filter_skips_outgoing_and_preserves_metadata():
    messages = [SimpleNamespace(id=30, out=True), SimpleNamespace(id=29, out=False)]
    iterate = Mock(return_value=stream(messages))
    mt = mt_client(iter_messages=iterate)
    mt._message_dict = lambda msg: {"id": msg.id, "file": {"title": "Track"}}
    result = await mt.search_media("123", kind="music", limit=1)
    assert result["messages"] == [{"id": 29, "file": {"title": "Track"}}]
    assert iterate.call_args.args == (123,)
    assert isinstance(iterate.call_args.kwargs["filter"], types.InputMessagesFilterMusic)


@pytest.mark.asyncio
async def test_ambiguous_contact_cannot_save():
    mt = mt_client()
    mt.resolve_chat = AsyncMock(return_value={"match_count": 2, "matches": [], "ambiguous": True})
    mt.forward_message = AsyncMock()
    result = await mt.save_latest_media("Admin", tag_name="music")
    assert result["needs_clarification"]
    mt.forward_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_and_tag_partial_failure_is_not_success():
    mt = mt_client()
    mt.resolve_chat = AsyncMock(
        return_value={"match_count": 1, "matches": [{"id": "123", "name": "Admin"}]}
    )
    mt.search_media = AsyncMock(return_value={"messages": [{"id": 29, "file": {"title": "Track"}}]})
    mt.forward_message = AsyncMock(return_value={"ok": True, "message_id": 88})
    mt.tag_saved_message = AsyncMock(side_effect=RuntimeError("test"))
    result = await mt.save_latest_media("Admin", tag_name="music")
    assert result["saved"] and result["ok"] is False
    assert result["saved_message_id"] == 88
    mt.forward_message.assert_awaited_once_with("me", "123", 29)
    mt.search_media.assert_awaited_once_with("123", kind="music", incoming_only=True, limit=1)


@pytest.mark.asyncio
async def test_native_tag_does_not_overwrite_different_title():
    mt = mt_client(get_me=AsyncMock(return_value=SimpleNamespace(id=42, premium=True)))
    mt.saved_tags = AsyncMock(return_value=[{"emoji": "🎵", "title": "work", "count": 2}])
    result = await mt.tag_saved_message(88, "music", "🎵")
    assert result["error"] == "TagEmojiAlreadyNamed"


@pytest.mark.asyncio
async def test_tag_preserves_selected_reactions_and_reuses_name():
    existing = types.ReactionEmoji("🔥")
    messages = SimpleNamespace(
        peer_id=types.PeerUser(42),
        reactions=SimpleNamespace(results=[SimpleNamespace(reaction=existing, chosen_order=0)]),
    )
    requests = []

    class Client:
        def is_connected(self):
            return True

        get_me = AsyncMock(return_value=SimpleNamespace(id=42, premium=True))
        get_messages = AsyncMock(return_value=messages)

        async def __call__(self, request):
            requests.append(request)
            return True

    mt = mt_client()
    mt.client = Client()
    mt.saved_tags = AsyncMock(return_value=[{"emoji": "🎵", "title": "music", "count": 2}])
    result = await mt.tag_saved_message(88, "music", "🎵")
    assert result["ok"]
    assert len(requests) == 1 and isinstance(requests[0], functions.messages.SendReactionRequest)
    assert [r.emoticon for r in requests[0].reaction] == ["🔥", "🎵"]


@pytest.mark.asyncio
async def test_tag_cannot_target_other_chat():
    mt = mt_client(
        get_me=AsyncMock(return_value=SimpleNamespace(id=42, premium=True)),
        get_messages=AsyncMock(return_value=SimpleNamespace(peer_id=types.PeerUser(99))),
    )
    mt.saved_tags = AsyncMock(return_value=[])
    assert (await mt.tag_saved_message(88, "music", "🎵"))["error"] == "SavedMessageNotFound"


@pytest.mark.asyncio
async def test_copy_protected_media_is_denied():
    message = SimpleNamespace(peer_id=types.PeerUser(42), media=object(), noforwards=True)
    send = AsyncMock()
    mt = mt_client(
        get_messages=AsyncMock(return_value=message),
        get_entity=AsyncMock(return_value=types.User(id=42)),
        send_file=send,
    )
    assert (await mt.copy_media("42", 1))["error"] == "ProtectedContent"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_generated_file_never_reads_arbitrary_path():
    send = AsyncMock(return_value=SimpleNamespace(id=10))
    mt = mt_client(send_file=send)
    with pytest.raises(ValueError):
        await mt.send_generated_file("me", "../secret.txt", "test")
    send.assert_not_awaited()
    assert (await mt.send_generated_file("me", "plan.md", "# Plan"))["ok"]
    assert send.call_args.args[1].getvalue() == b"# Plan"


@pytest.mark.asyncio
async def test_join_rejects_non_telegram_invite():
    mt = mt_client()
    with pytest.raises(ValueError):
        await mt.join_channel("https://example.com/+abcd")


@pytest.mark.asyncio
async def test_chained_function_call_preserves_signature_id_and_deduplicates():
    call = {
        "name": "save_latest_media",
        "args": {"chat_name": "Admin", "tag_name": "music"},
        "id": "call1",
    }
    mt = mt_client()
    mt.save_latest_media = AsyncMock(
        return_value={"ok": True, "saved": True, "saved_message_id": 88}
    )
    agent, snapshots = make_agent(
        mt,
        [
            response(
                {"text": "Internal plan", "thought": True},
                {"functionCall": call, "thoughtSignature": "opaque"},
            ),
            response({"functionCall": {**call, "id": "call2"}}),
            response({"text": "Musiqani Saved Messagesga saqladim va music tegini qo‘ydim."}),
        ],
    )
    result = await agent.chat(
        chat_id=42, text="Admin musiqasini Savedga music tag bilan saqla", allow_account_tools=True
    )
    assert result.startswith("Musiqani") and "Internal plan" not in result
    assert mt.save_latest_media.await_count == 1
    assert snapshots[1]["contents"][1]["parts"][1]["thoughtSignature"] == "opaque"
    assert snapshots[1]["contents"][2]["parts"][0]["functionResponse"]["id"] == "call1"


@pytest.mark.asyncio
async def test_bot_reaction_target_is_server_bound():
    agent, _ = make_agent(mt_client(), [])
    agent.bot_reactor = AsyncMock(return_value={"ok": True})
    result = await agent._execute_tool(
        "react_to_user_message",
        {"emoji": "👍", "chat_id": 999, "message_id": 777},
        42,
        message_id=10,
    )
    assert result["ok"]
    agent.bot_reactor.assert_awaited_once_with(42, 10, emoji="👍")


@pytest.mark.asyncio
async def test_extended_mutations_denied_without_owner():
    agent, _ = make_agent(mt_client(), [])
    for name in agent.writes.ACTION_METHODS:
        assert (await agent._execute_tool(name, {}, 99))["error"] == "AccountAccessDenied"


@pytest.mark.parametrize("action", sorted(HIGH_IMPACT_ACTIONS))
def test_high_impact_always_needs_confirmation(action):
    assert evaluate_action(
        action, explicit_current_request=True, require_confirmation=False
    ).requires_confirmation


@pytest.mark.asyncio
async def test_natural_confirmation_and_callback_are_single_use():
    obj, _ = runtime()
    obj.writes.execute_confirmed = AsyncMock(return_value={"ok": True})
    pending = obj.confirmations.create(42, "leave_channel", {"peer": "@test"})
    query = SimpleNamespace(
        message=SimpleNamespace(chat_id=42, chat=SimpleNamespace(type="private")),
        from_user=SimpleNamespace(id=99),
        data=f"confirm:{pending.token}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    await obj._confirmation_callback(query)
    obj.writes.execute_confirmed.assert_not_awaited()
    assert len(obj.confirmations.for_chat(42)) == 1
    u = update("tasdiqlayman")
    await obj.handle_update(u, None)
    obj.writes.execute_confirmed.assert_awaited_once()
    assert "{" not in u.effective_message.reply_text.call_args.args[0]
    query.from_user.id = 42
    await obj._confirmation_callback(query)
    assert obj.writes.execute_confirmed.await_count == 1


@pytest.mark.asyncio
async def test_ai_confirmation_has_buttons_without_exposing_token():
    obj, _ = runtime()

    async def chat(**kwargs):
        obj.confirmations.create(42, "leave_channel", {"peer": "@test"})
        return "Kanaldan chiqishni tasdiqlaysizmi?"

    obj.agent.chat = chat
    u = update("Kanaldan chiq")
    await obj.handle_update(u, None)
    call = u.effective_message.reply_text.call_args
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].text == "Tasdiqlash"
    assert next(iter(obj.confirmations._pending)) not in call.args[0]


@pytest.mark.asyncio
async def test_bot_reaction_backend_standard_and_custom():
    obj, _ = runtime()
    bot = SimpleNamespace(set_message_reaction=AsyncMock(return_value=True))
    obj.application = SimpleNamespace(running=True, bot=bot)
    assert (await obj.react_to_message(42, 10, emoji="👍"))["ok"]
    assert (await obj.react_to_message(42, 10, custom_emoji_id="123"))["ok"]
    assert bot.set_message_reaction.await_count == 2


@pytest.mark.asyncio
async def test_permanent_ai_error_is_not_retried_and_body_is_not_leaked():
    client = GeminiClient(Settings(_env_file=None, gemini_api_key="test-key"))
    await client.client.aclose()
    transport = httpx.MockTransport(lambda request: httpx.Response(402, text="PRIVATE_ERROR_BODY"))
    client.client = httpx.AsyncClient(transport=transport)
    with pytest.raises(GeminiError) as error:
        await client._post("test-model", {})
    assert error.value.status_code == 402
    assert "PRIVATE_ERROR_BODY" not in str(error.value)
    await client.close()


@pytest.mark.asyncio
async def test_auto_tag_uses_server_defaults_without_renaming_existing_tags():
    requests = []

    class Client:
        def is_connected(self):
            return True

        get_me = AsyncMock(return_value=SimpleNamespace(id=42, premium=True))
        get_messages = AsyncMock(
            return_value=SimpleNamespace(peer_id=types.PeerUser(42), reactions=None)
        )

        async def __call__(self, request):
            requests.append(request)
            if isinstance(request, functions.messages.GetDefaultTagReactionsRequest):
                return SimpleNamespace(
                    reactions=[types.ReactionEmoji("🔥"), types.ReactionEmoji("👍")]
                )
            return True

    mt = mt_client()
    mt.client = Client()
    mt.saved_tags = AsyncMock(
        return_value=[
            {"emoji": "🔥", "title": "work", "count": 2},
            {"custom_emoji_id": "123", "title": "custom", "count": 1},
        ]
    )
    result = await mt.tag_saved_message(88, "music")
    assert result["ok"] and result["emoji"] == "👍"
    assert requests[-1].title == "music" and requests[-1].reaction.emoticon == "👍"


def test_known_secrets_are_redacted_before_model_context():
    agent, _ = make_agent(mt_client(), [])
    agent.settings.telegram_session = "private-session-example"
    agent.settings.telegram_api_hash = "private-api-hash-example"
    safe = agent._safe_data(
        {
            "messages": ["prefix private-session-example private-api-hash-example"],
            "password": "never-send-this",
        }
    )
    assert safe == {"messages": ["prefix [REDACTED] [REDACTED]"], "password": "[REDACTED]"}


@pytest.mark.asyncio
async def test_revoked_session_does_not_trigger_login(monkeypatch):
    client = SimpleNamespace(
        connect=AsyncMock(),
        is_user_authorized=AsyncMock(return_value=False),
        disconnect=AsyncMock(),
        get_me=AsyncMock(),
        sign_in=AsyncMock(),
    )
    factory = Mock(return_value=client)
    monkeypatch.setattr("app.telegram.mtproto_client.TelegramClient", factory)
    monkeypatch.setattr("app.telegram.mtproto_client.StringSession", lambda value: object())
    settings = Settings(
        _env_file=None,
        telegram_api_id=1,
        telegram_api_hash="dummy-hash",
        telegram_session="dummy-session",
    )
    mt = MTProtoClient(settings)
    with pytest.raises(MTProtoUnavailable):
        await mt.start()
    assert mt.client is None
    client.disconnect.assert_awaited_once()
    client.sign_in.assert_not_awaited()
    client.get_me.assert_not_awaited()
    assert factory.call_args.kwargs["device_model"] == "Veltrix Telegram Agent"


def test_offline_suite_blocks_telegram_dns():
    import socket

    with pytest.raises(RuntimeError, match="External DNS disabled"):
        socket.getaddrinfo("api.telegram.org", 443)


@pytest.mark.asyncio
async def test_channel_edit_reports_partial_success_without_retry():
    requests = []

    class Client:
        def is_connected(self):
            return True

        get_input_entity = AsyncMock(return_value=types.InputPeerChannel(1, 2))

        async def __call__(self, request):
            requests.append(request)
            if isinstance(request, functions.messages.EditChatAboutRequest):
                raise RuntimeError("private details")
            return True

    mt = mt_client()
    mt.client = Client()
    result = await mt.edit_channel_info("@channel", title="New", about="About")
    assert result == {"ok": False, "applied": ["title"], "error": "RuntimeError"}
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_sticker_source_cannot_target_other_chat():
    mt = mt_client(
        get_messages=AsyncMock(
            return_value=SimpleNamespace(sticker=True, peer_id=types.PeerUser(99))
        ),
        get_entity=AsyncMock(return_value=types.User(id=42)),
    )
    with pytest.raises(ValueError, match="selected chat"):
        await mt._sticker_document("42", 1)


@pytest.mark.asyncio
async def test_confirmation_reply_failure_never_reexecutes_or_claims_action_failed():
    obj, _ = runtime()
    obj.writes.execute_confirmed = AsyncMock(return_value={"ok": True})
    pending = obj.confirmations.create(42, "leave_channel", {"peer": "@test"})
    query = SimpleNamespace(
        message=SimpleNamespace(chat_id=42, chat=SimpleNamespace(type="private")),
        from_user=SimpleNamespace(id=42),
        data=f"confirm:{pending.token}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(side_effect=RuntimeError("reply unavailable")),
    )
    await obj._confirmation_callback(query)
    assert query.edit_message_text.await_count == 1
    assert query.edit_message_text.call_args.args[0] == "Tasdiqlangan amal bajarildi."
    await obj._confirmation_callback(query)
    obj.writes.execute_confirmed.assert_awaited_once()


@pytest.mark.asyncio
async def test_billing_failure_circuit_does_not_keep_calling_provider(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.ai.gemini_client.time.monotonic", lambda: now[0])
    client = GeminiClient(Settings(_env_file=None, gemini_api_key="dummy"))
    await client.client.aclose()
    requests = []

    def provider(request):
        requests.append(request)
        return (
            httpx.Response(402, text="PRIVATE")
            if len(requests) == 1
            else httpx.Response(200, json={"ok": True})
        )

    client.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    for _ in range(2):
        with pytest.raises(GeminiError) as error:
            await client._post("test", {})
        assert error.value.status_code == 402
    assert len(requests) == 1
    now[0] = 161.0
    assert await client._post("test", {}) == {"ok": True}
    assert len(requests) == 2
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [402, 401, 429, 503])
async def test_ai_failure_never_redirects_user_to_manual_commands(status):
    obj, _ = runtime()
    obj.agent.chat = AsyncMock(side_effect=GeminiError("sanitized", status))
    obj.writes.execute_confirmed = AsyncMock()
    u = update("Admin musiqasini Savedga saqla")
    await obj.handle_update(u, None)
    reply = u.effective_message.reply_text.call_args.args[0]
    assert "Buyruq yozishingiz kerak emas" in reply
    assert not any(command in reply for command in ["/do", "/account", "/chats", "/status"])
    obj.writes.execute_confirmed.assert_not_awaited()


@pytest.mark.asyncio
async def test_welcome_is_natural_language_without_command_catalog():
    obj, _ = runtime()
    u = update("/start")
    await obj.handle_update(u, None)
    reply = u.effective_message.reply_text.call_args.args[0]
    assert "o‘z tilingizda" in reply
    assert "/do" not in reply and "JSON yozishingiz shart emas" in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_ok", [True, False])
async def test_readiness_probe_is_synthetic_and_does_not_invoke_account_tools(provider_ok):
    client = GeminiClient(Settings(_env_file=None, gemini_api_key="dummy"))
    if provider_ok:
        client._post = AsyncMock(
            return_value=response(
                {"functionCall": {"name": "readiness_ping", "args": {"value": "ready"}}}
            )
        )
    else:
        client._post = AsyncMock(side_effect=GeminiError("sanitized", 402))
    assert await client.probe_readiness() is provider_ok
    assert client.readiness_probe_passed is provider_ok
    payload = client._post.call_args.args[1]
    config = payload["toolConfig"]["functionCallingConfig"]
    assert config == {"mode": "ANY", "allowedFunctionNames": ["readiness_ping"]}
    assert len(payload["tools"][0]["functionDeclarations"]) == 1
    assert "Telegram" not in payload["contents"][0]["parts"][0]["text"]
    await client.close()


@pytest.mark.asyncio
async def test_readiness_probe_does_not_accept_plain_http_success_as_tool_readiness():
    client = GeminiClient(Settings(_env_file=None, gemini_api_key="dummy"))
    client._post = AsyncMock(return_value=response({"text": "hello"}))
    assert await client.probe_readiness() is False
    await client.close()
