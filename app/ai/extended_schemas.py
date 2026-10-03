from __future__ import annotations


def declaration(name, description, properties=None, required=()):
    parameters = {"type": "OBJECT", "properties": properties or {}}
    if required:
        parameters["required"] = list(required)
    return {"name": name, "description": description, "parameters": parameters}


S = {"type": "STRING"}
INT = {"type": "INTEGER"}
B = {"type": "BOOLEAN"}
PEER = {
    "type": "STRING",
    "description": "me for Saved Messages, @username, or verified numeric peer ID",
}
ADMIN_RIGHTS = {
    "type": "OBJECT",
    "properties": {
        k: B
        for k in (
            "change_info",
            "post_messages",
            "edit_messages",
            "delete_messages",
            "ban_users",
            "invite_users",
            "pin_messages",
            "add_admins",
            "manage_call",
            "manage_topics",
            "post_stories",
            "edit_stories",
            "delete_stories",
            "manage_direct_messages",
        )
    },
}

EXTENDED_DECLARATIONS = [
    declaration(
        "resolve_chat",
        "Find a contact or existing chat by the name the owner uses (including contact names), not global public search. Ambiguous results require clarification before writes.",
        {"query": S, "limit": INT},
        ("query",),
    ),
    declaration(
        "search_media",
        "Find newest-first Telegram music/voice/photo/video/file/gif messages. Default incoming_only true excludes owner's outgoing media. Use offset_id to continue a bounded search.",
        {"peer": PEER, "kind": S, "incoming_only": B, "limit": INT, "offset_id": INT},
        ("peer",),
    ),
    declaration(
        "saved_tags",
        "List actual Premium Saved Messages tags and names. Reuse an existing matching tag.",
        {},
    ),
    declaration(
        "available_reactions",
        "Get currently available standard Telegram user reactions. Chat restrictions and Premium still apply.",
        {},
    ),
    declaration(
        "save_latest_media",
        "Execute the owner's explicit request such as 'Save the latest music Admin sent me and tag it music': resolve local contact/chat name, choose newest incoming media, forward to Saved Messages, optionally add a real Premium tag. Does not guess among ambiguous contacts. Returns partial success if saved but tagging fails; do NOT retry forwarding.",
        {"chat_name": S, "kind": S, "tag_name": S, "tag_emoji": S},
        ("chat_name",),
    ),
    declaration(
        "tag_saved_message",
        "Add/reuse a named native Premium Saved Messages reaction tag, preserving other tags. Name up to 12 characters. Never rename an existing different tag silently.",
        {"message_id": INT, "tag_name": S, "emoji": S},
        ("message_id", "tag_name"),
    ),
    declaration(
        "react_message",
        "React as the owner to a verified MTProto message ID, standard or custom emoji; remove clears owner's reactions. Never paid reactions. To react as BOT to the current user message use react_to_user_message instead.",
        {"peer": PEER, "message_id": INT, "emoji": S, "custom_emoji_id": S, "remove": B, "big": B},
        ("peer", "message_id"),
    ),
    declaration(
        "react_to_user_message",
        "React as the BOT to the user's current message in this bot conversation. Target is fixed by the server. One allowed standard/custom reaction; custom only if Telegram permits it. No paid reactions. Use when requested or contextually appropriate, never pretend success.",
        {"emoji": S, "custom_emoji_id": S, "remove": B},
    ),
    declaration(
        "copy_media",
        "Send existing accessible Telegram media to another chat with optional new caption, only on owner's request. Protected content is not copied. Use forward_message to preserve source attribution.",
        {"source": PEER, "message_id": INT, "target": PEER, "caption": S},
        ("source", "message_id"),
    ),
    declaration(
        "send_generated_file",
        "Create and send a creative UTF-8 text/Markdown/CSV/JSON/SVG/HTML file from content you write; max 512 KB. Does not execute code or read server files. Only on explicit request.",
        {"peer": PEER, "filename": S, "content": S},
        ("peer", "filename", "content"),
    ),
    declaration(
        "channel_info",
        "Read channel/supergroup details and the owner's actual admin rights before management; do not invent permissions.",
        {"peer": PEER},
        ("peer",),
    ),
    declaration(
        "channel_members",
        "Read a bounded list of visible channel/supergroup members, subject to rights.",
        {"peer": PEER, "limit": INT},
        ("peer",),
    ),
    declaration(
        "join_channel",
        "Join one Telegram channel/group by username or t.me invite link ONLY when owner requests. Approval-required invitations may remain pending. No bulk joining.",
        {"peer": S},
        ("peer",),
    ),
    declaration(
        "leave_channel",
        "Leave one channel/supergroup. Confirmation required.",
        {"peer": PEER},
        ("peer",),
    ),
    declaration(
        "create_channel",
        "Create a channel or supergroup with title/about on owner's request. Confirmation required.",
        {"title": S, "about": S, "megagroup": B},
        ("title",),
    ),
    declaration(
        "edit_channel_info",
        "Edit a channel/supergroup title/about when owner has rights. Confirmation required.",
        {"peer": PEER, "title": S, "about": S},
        ("peer",),
    ),
    declaration(
        "set_channel_admin",
        "Grant/revoke specified admin rights to one verified user. Check channel_info first. Confirmation required, never implicitly grant add_admins.",
        {"peer": PEER, "user": PEER, "rights": ADMIN_RIGHTS, "rank": S},
        ("peer", "user", "rights"),
    ),
    declaration(
        "ban_channel_member",
        "Ban/unban one user from a managed channel/group, with confirmation.",
        {"peer": PEER, "user": PEER, "banned": B},
        ("peer", "user"),
    ),
    declaration(
        "sticker_set_info",
        "Get a sticker pack by short name or t.me/addstickers URL, and bounded sticker indexes for sending.",
        {"short_name": S, "limit": INT},
        ("short_name",),
    ),
    declaration("installed_sticker_sets", "List the owner's installed standard sticker packs.", {}),
    declaration(
        "install_sticker_set",
        "Add one sticker pack to the owner's account on request.",
        {"short_name": S},
        ("short_name",),
    ),
    declaration(
        "uninstall_sticker_set",
        "Remove one installed sticker pack. Confirmation required.",
        {"short_name": S},
        ("short_name",),
    ),
    declaration(
        "send_sticker",
        "Send a sticker from a verified pack/index as the owner, only on request.",
        {"peer": PEER, "short_name": S, "index": INT},
        ("peer", "short_name"),
    ),
    declaration(
        "favorite_sticker",
        "Add/remove an existing Telegram sticker message to/from owner's favorites.",
        {"source": PEER, "message_id": INT, "remove": B},
        ("source", "message_id"),
    ),
    declaration(
        "create_sticker_set",
        "Create a personal sticker pack from an existing accessible sticker message (not arbitrary images). Confirmation required. Title, new short_name and emoji are mandatory.",
        {"title": S, "short_name": S, "source": PEER, "message_id": INT, "emoji": S},
        ("title", "short_name", "source", "message_id", "emoji"),
    ),
    declaration(
        "add_sticker_to_set",
        "Add an existing sticker message to a sticker pack the account controls. Confirmation required.",
        {"short_name": S, "source": PEER, "message_id": INT, "emoji": S},
        ("short_name", "source", "message_id", "emoji"),
    ),
]

EXTENDED_READS = {
    name: name
    for name in (
        "resolve_chat",
        "search_media",
        "saved_tags",
        "available_reactions",
        "channel_info",
        "channel_members",
        "sticker_set_info",
        "installed_sticker_sets",
    )
}
