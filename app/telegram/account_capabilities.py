from __future__ import annotations

import io
import re
from urllib.parse import urlparse

from telethon import functions, types, utils


class AccountCapabilities:
    """Bounded account operations. No arbitrary RPC, local paths, or code execution."""

    @staticmethod
    def _peer(peer):
        return int(peer) if isinstance(peer, str) and peer.lstrip("-").isdigit() else peer

    @staticmethod
    def _entity_summary(entity, name=None):
        return {
            "id": str(utils.get_peer_id(entity)),
            "name": name or utils.get_display_name(entity),
            "username": getattr(entity, "username", None),
            "bot": bool(getattr(entity, "bot", False)),
        }

    async def resolve_chat(self, query: str, limit: int = 10):
        query = query.strip().casefold().lstrip("@")
        if not query:
            raise ValueError("Chat name is required")
        client = self._require()
        candidates = {}
        contacts = await client(functions.contacts.GetContactsRequest(hash=0))
        rows = [(u, utils.get_display_name(u)) for u in contacts.users]
        async for dialog in client.iter_dialogs(limit=500):
            rows.append((dialog.entity, dialog.name))
        exact = set()
        for entity, name in rows:
            aliases = {
                str(name or "").casefold(),
                utils.get_display_name(entity).casefold(),
                str(getattr(entity, "username", "") or "").casefold(),
            }
            row = self._entity_summary(entity, name)
            if query in aliases:
                exact.add(row["id"])
            if any(query in alias for alias in aliases):
                candidates[row["id"]] = row
        matches = [row for key, row in candidates.items() if not exact or key in exact]
        return {
            "matches": matches[: max(1, min(limit, 20))],
            "match_count": len(matches),
            "ambiguous": len(matches) > 1,
            "scope": "contacts and up to 500 recent dialogs",
        }

    async def search_media(
        self,
        peer,
        kind: str = "music",
        incoming_only: bool = True,
        limit: int = 10,
        offset_id: int = 0,
    ):
        filters = {
            "music": types.InputMessagesFilterMusic,
            "voice": types.InputMessagesFilterVoice,
            "photo": types.InputMessagesFilterPhotos,
            "video": types.InputMessagesFilterVideo,
            "file": types.InputMessagesFilterDocument,
            "gif": types.InputMessagesFilterGif,
        }
        if kind not in filters:
            raise ValueError("Unsupported media kind")
        result = []
        async for message in self._require().iter_messages(
            self._peer(peer), limit=200, filter=filters[kind](), offset_id=offset_id
        ):
            if incoming_only and message.out:
                continue
            result.append(self._message_dict(message))
            if len(result) >= max(1, min(limit, 50)):
                break
        return {"messages": result, "order": "newest first", "scan_limit": 200}

    @staticmethod
    def _reaction(emoji: str | None = None, custom_emoji_id: str | None = None):
        if bool(emoji) == bool(custom_emoji_id):
            raise ValueError("Choose one emoji or custom emoji ID")
        return (
            types.ReactionCustomEmoji(int(custom_emoji_id))
            if custom_emoji_id
            else types.ReactionEmoji(emoji)
        )

    @staticmethod
    def _reaction_dict(reaction):
        if isinstance(reaction, types.ReactionEmoji):
            return {"emoji": reaction.emoticon}
        if isinstance(reaction, types.ReactionCustomEmoji):
            return {"custom_emoji_id": str(reaction.document_id)}
        return {"unsupported": type(reaction).__name__}

    async def available_reactions(self):
        result = await self._require()(functions.messages.GetAvailableReactionsRequest(hash=0))
        return [
            {"emoji": r.reaction, "premium": bool(getattr(r, "premium", False))}
            for r in getattr(result, "reactions", [])
            if not getattr(r, "inactive", False)
        ]

    async def saved_tags(self):
        result = await self._require()(functions.messages.GetSavedReactionTagsRequest(hash=0))
        return [
            {**self._reaction_dict(t.reaction), "title": t.title, "count": t.count}
            for t in getattr(result, "tags", [])
        ]

    async def react_message(
        self,
        peer,
        message_id: int,
        emoji: str | None = None,
        custom_emoji_id: str | None = None,
        remove: bool = False,
        big: bool = False,
    ):
        client = self._require()
        reactions = [] if remove else [self._reaction(emoji, custom_emoji_id)]
        await client(
            functions.messages.SendReactionRequest(
                peer=await client.get_input_entity(self._peer(peer)),
                msg_id=message_id,
                reaction=reactions,
                big=big,
            )
        )
        return {"ok": True, "message_id": message_id, "removed": remove}

    async def tag_saved_message(self, message_id: int, tag_name: str, emoji: str | None = None):
        if not tag_name.strip() or len(tag_name) > 12:
            raise ValueError("Saved tag name must be 1-12 characters")
        client = self._require()
        me = await client.get_me()
        if not getattr(me, "premium", False):
            return {"ok": False, "error": "PremiumRequired", "message_id": message_id}
        existing = await self.saved_tags()
        same = next(
            (t for t in existing if (t["title"] or "").casefold() == tag_name.casefold()), None
        )
        if same:
            reaction = self._reaction(same.get("emoji"), same.get("custom_emoji_id"))
        elif emoji:
            reaction = self._reaction(emoji)
        else:
            defaults = await client(functions.messages.GetDefaultTagReactionsRequest(hash=0))
            used = [{k: t[k] for k in ("emoji", "custom_emoji_id") if k in t} for t in existing]
            reaction = next(
                (
                    r
                    for r in getattr(defaults, "reactions", [])
                    if self._reaction_dict(r) not in used
                ),
                None,
            )
            if reaction is None:
                return {"ok": False, "error": "ChooseTagEmoji", "message_id": message_id}
        reaction_key = self._reaction_dict(reaction)
        if not same and any(
            {k: t[k] for k in ("emoji", "custom_emoji_id") if k in t} == reaction_key
            and t.get("title") not in {None, "", tag_name}
            for t in existing
        ):
            return {
                "ok": False,
                "error": "TagEmojiAlreadyNamed",
                "message_id": message_id,
                "message": "Choose a different tag emoji; existing names were not overwritten.",
            }
        message = await client.get_messages("me", ids=message_id)
        if not message or utils.get_peer_id(message.peer_id) != me.id:
            return {"ok": False, "error": "SavedMessageNotFound"}
        if getattr(message, "reactions", None) and not getattr(
            message.reactions, "reactions_as_tags", True
        ):
            return {
                "ok": False,
                "error": "LegacyReactionsRequireMigration",
                "message_id": message_id,
            }
        chosen = [
            r.reaction
            for r in getattr(getattr(message, "reactions", None), "results", [])
            if getattr(r, "chosen_order", None) is not None
        ]
        if reaction not in chosen:
            chosen.append(reaction)
        await client(
            functions.messages.SendReactionRequest(
                peer=types.InputPeerSelf(), msg_id=message_id, reaction=chosen
            )
        )
        if not same:
            await client(
                functions.messages.UpdateSavedReactionTagRequest(reaction=reaction, title=tag_name)
            )
        return {
            "ok": True,
            "message_id": message_id,
            "tag": tag_name,
            **self._reaction_dict(reaction),
        }

    async def save_latest_media(
        self,
        chat_name: str,
        kind: str = "music",
        tag_name: str | None = None,
        tag_emoji: str | None = None,
    ):
        resolved = await self.resolve_chat(chat_name)
        if resolved["match_count"] != 1:
            return {"ok": False, "needs_clarification": True, **resolved}
        source = resolved["matches"][0]
        result = await self.search_media(source["id"], kind=kind, incoming_only=True, limit=1)
        if not result["messages"]:
            return {"ok": False, "error": "NoIncomingMediaFound", "source": source}
        original = result["messages"][0]
        saved = await self.forward_message("me", source["id"], original["id"])
        outcome = {
            "ok": True,
            "saved": True,
            "source": source,
            "original_message_id": original["id"],
            "saved_message_id": saved["message_id"],
            "media": original.get("file"),
        }
        if tag_name:
            try:
                tagged = await self.tag_saved_message(saved["message_id"], tag_name, tag_emoji)
                outcome["tag_result"] = tagged
                outcome["ok"] = bool(tagged["ok"])
            except Exception as exc:
                # Do not forward again if tagging fails: report the partial outcome.
                outcome.update(ok=False, tag_result={"ok": False, "error": type(exc).__name__})
        return outcome

    async def copy_media(self, source, message_id: int, target="me", caption: str | None = None):
        client = self._require()
        message = await client.get_messages(self._peer(source), ids=message_id)
        if not message or not message.media:
            raise ValueError("Source message has no media")
        source_entity = await client.get_entity(self._peer(source))
        if utils.get_peer_id(message.peer_id) != utils.get_peer_id(source_entity):
            return {"ok": False, "error": "MessagePeerMismatch"}
        if getattr(message, "noforwards", False) or getattr(source_entity, "noforwards", False):
            return {
                "ok": False,
                "error": "ProtectedContent",
                "message": "Copy protection will not be bypassed.",
            }
        sent = await client.send_file(
            self._peer(target), message.media, caption=caption, parse_mode=None
        )
        return {"ok": True, "message_id": sent.id}

    async def send_generated_file(self, peer, filename: str, content: str):
        if not re.fullmatch(r"[\w .-]{1,80}\.(txt|md|csv|json|svg|html)", filename, re.IGNORECASE):
            raise ValueError("Use a simple txt/md/csv/json/svg/html filename")
        data = content.encode("utf-8")
        if len(data) > 512_000:
            raise ValueError("Generated file exceeds 512 KB")
        stream = io.BytesIO(data)
        stream.name = filename
        sent = await self._require().send_file(self._peer(peer), stream, force_document=True)
        return {"ok": True, "message_id": sent.id, "filename": filename}

    async def channel_info(self, peer):
        client = self._require()
        entity = await client.get_entity(self._peer(peer))
        full = await client(functions.channels.GetFullChannelRequest(entity))
        return {
            **self._entity_summary(entity),
            "about": full.full_chat.about,
            "participants_count": getattr(full.full_chat, "participants_count", None),
            "creator": bool(getattr(entity, "creator", False)),
            "admin_rights": getattr(entity, "admin_rights", None).to_dict()
            if getattr(entity, "admin_rights", None)
            else None,
        }

    async def channel_members(self, peer, limit: int = 20):
        members = await self._require().get_participants(
            self._peer(peer), limit=max(1, min(limit, 100))
        )
        return [self._entity_summary(u) for u in members]

    async def join_channel(self, peer: str):
        client = self._require()
        invite = None
        if peer.startswith("https://"):
            parsed = urlparse(peer)
            if parsed.hostname not in {"t.me", "telegram.me"}:
                raise ValueError("Only Telegram invite links are accepted")
            path = parsed.path.strip("/")
            if path.startswith("+") or path.startswith("joinchat/"):
                invite = path.removeprefix("+").removeprefix("joinchat/")
            else:
                peer = path
        if invite:
            check = await client(functions.messages.CheckChatInviteRequest(invite))
            if isinstance(check, types.ChatInviteAlready):
                return {
                    "ok": True,
                    "already_joined": True,
                    "chat": self._entity_summary(check.chat),
                }
            result = await client(functions.messages.ImportChatInviteRequest(invite))
        else:
            result = await client(
                functions.channels.JoinChannelRequest(
                    await client.get_input_entity(self._peer(peer))
                )
            )
        return {
            "ok": True,
            "chats": [self._entity_summary(c) for c in getattr(result, "chats", [])],
        }

    async def leave_channel(self, peer):
        client = self._require()
        await client(
            functions.channels.LeaveChannelRequest(await client.get_input_entity(self._peer(peer)))
        )
        return {"ok": True}

    async def create_channel(self, title: str, about: str = "", megagroup: bool = False):
        result = await self._require()(
            functions.channels.CreateChannelRequest(
                title=title, about=about, megagroup=megagroup, broadcast=not megagroup
            )
        )
        return {"ok": True, "chats": [self._entity_summary(c) for c in result.chats]}

    async def edit_channel_info(self, peer, title: str | None = None, about: str | None = None):
        if title is None and about is None:
            raise ValueError("Provide a title or description")
        client = self._require()
        entity = await client.get_input_entity(self._peer(peer))
        applied = []
        try:
            if title is not None:
                await client(functions.channels.EditTitleRequest(entity, title))
                applied.append("title")
            if about is not None:
                await client(functions.messages.EditChatAboutRequest(entity, about))
                applied.append("about")
        except Exception as exc:
            if not applied:
                raise
            return {"ok": False, "applied": applied, "error": type(exc).__name__}
        return {"ok": True, "applied": applied}

    async def set_channel_admin(self, peer, user, rights: dict, rank: str = "Admin"):
        client = self._require()
        if any(not isinstance(v, bool) for v in rights.values()):
            raise ValueError("Admin rights must be booleans")
        admin = types.ChatAdminRights(**rights)
        await client(
            functions.channels.EditAdminRequest(
                channel=await client.get_input_entity(self._peer(peer)),
                user_id=await client.get_input_entity(self._peer(user)),
                admin_rights=admin,
                rank=rank,
            )
        )
        return {"ok": True}

    async def ban_channel_member(self, peer, user, banned: bool = True):
        client = self._require()
        await client(
            functions.channels.EditBannedRequest(
                channel=await client.get_input_entity(self._peer(peer)),
                participant=await client.get_input_entity(self._peer(user)),
                banned_rights=types.ChatBannedRights(until_date=None, view_messages=banned),
            )
        )
        return {"ok": True, "banned": banned}

    @staticmethod
    def _sticker_set(short_name: str):
        short_name = short_name.removeprefix("https://t.me/addstickers/")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", short_name):
            raise ValueError("Invalid sticker set short name")
        return types.InputStickerSetShortName(short_name)

    async def sticker_set_info(self, short_name: str, limit: int = 20):
        result = await self._require()(
            functions.messages.GetStickerSetRequest(self._sticker_set(short_name), hash=0)
        )
        return {
            "title": result.set.title,
            "short_name": result.set.short_name,
            "count": result.set.count,
            "stickers": [
                {"index": i, "id": str(d.id), "mime_type": d.mime_type}
                for i, d in enumerate(result.documents[: max(1, min(limit, 100))])
            ],
        }

    async def installed_sticker_sets(self):
        result = await self._require()(functions.messages.GetAllStickersRequest(hash=0))
        return [
            {"title": s.title, "short_name": s.short_name, "count": s.count}
            for s in getattr(result, "sets", [])[:100]
        ]

    async def install_sticker_set(self, short_name: str):
        await self._require()(
            functions.messages.InstallStickerSetRequest(
                self._sticker_set(short_name), archived=False
            )
        )
        return {"ok": True, "short_name": short_name}

    async def uninstall_sticker_set(self, short_name: str):
        await self._require()(
            functions.messages.UninstallStickerSetRequest(self._sticker_set(short_name))
        )
        return {"ok": True}

    async def send_sticker(self, peer, short_name: str, index: int = 0):
        client = self._require()
        result = await client(
            functions.messages.GetStickerSetRequest(self._sticker_set(short_name), hash=0)
        )
        if not 0 <= index < len(result.documents):
            raise ValueError("Sticker index out of range")
        sent = await client.send_file(self._peer(peer), result.documents[index])
        return {"ok": True, "message_id": sent.id}

    async def _sticker_document(self, source, message_id):
        client = self._require()
        message = await client.get_messages(self._peer(source), ids=message_id)
        if not message or not message.sticker:
            raise ValueError("Source is not a sticker")
        entity = await client.get_entity(self._peer(source))
        if utils.get_peer_id(message.peer_id) != utils.get_peer_id(entity):
            raise ValueError("Sticker message does not belong to the selected chat")
        return utils.get_input_document(message.document)

    async def favorite_sticker(self, source, message_id: int, remove: bool = False):
        await self._require()(
            functions.messages.FaveStickerRequest(
                id=await self._sticker_document(source, message_id), unfave=remove
            )
        )
        return {"ok": True, "removed": remove}

    async def create_sticker_set(
        self, title: str, short_name: str, source, message_id: int, emoji: str
    ):
        self._sticker_set(short_name)
        client = self._require()
        document = await self._sticker_document(source, message_id)
        result = await client(
            functions.stickers.CreateStickerSetRequest(
                user_id=types.InputUserSelf(),
                title=title,
                short_name=short_name,
                stickers=[types.InputStickerSetItem(document=document, emoji=emoji)],
            )
        )
        return {"ok": True, "short_name": result.set.short_name}

    async def add_sticker_to_set(self, short_name: str, source, message_id: int, emoji: str):
        document = await self._sticker_document(source, message_id)
        await self._require()(
            functions.stickers.AddStickerToSetRequest(
                stickerset=self._sticker_set(short_name),
                sticker=types.InputStickerSetItem(document=document, emoji=emoji),
            )
        )
        return {"ok": True}
