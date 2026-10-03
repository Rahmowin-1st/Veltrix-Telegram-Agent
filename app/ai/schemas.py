from __future__ import annotations

from app.ai.chat_schemas import CHAT_DECLARATIONS
from app.ai.extended_schemas import EXTENDED_DECLARATIONS

TOOL_DECLARATIONS = CHAT_DECLARATIONS + [
    {
        "name": "account_info",
        "description": "Get the owner's current Telegram account/profile info via MTProto.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "recent_chats",
        "description": "List recent Telegram dialogs/chats with IDs and unread counts.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"limit": {"type": "INTEGER", "description": "1-100"}},
        },
    },
    {
        "name": "search_telegram",
        "description": "Search Telegram users/channels/groups by text or username.",
        "parameters": {
            "type": "OBJECT",
            "required": ["query"],
            "properties": {
                "query": {"type": "STRING"},
                "limit": {"type": "INTEGER"},
            },
        },
    },
    {
        "name": "recent_messages",
        "description": "Read a bounded recent message history from one Telegram peer.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer"],
            "properties": {"peer": {"type": "STRING"}, "limit": {"type": "INTEGER"}},
        },
    },
    {
        "name": "search_messages",
        "description": "Search messages inside a selected Telegram peer.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer", "query"],
            "properties": {
                "peer": {"type": "STRING"},
                "query": {"type": "STRING"},
                "limit": {"type": "INTEGER"},
            },
        },
    },
    {
        "name": "global_message_search",
        "description": "Search the owner's accessible Telegram messages globally via MTProto.",
        "parameters": {
            "type": "OBJECT",
            "required": ["query"],
            "properties": {"query": {"type": "STRING"}, "limit": {"type": "INTEGER"}},
        },
    },
    {
        "name": "list_contacts",
        "description": "List Telegram contacts visible to the owner account.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "entity_info",
        "description": "Get details about a Telegram user/chat/channel by peer ID or username.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer"],
            "properties": {"peer": {"type": "STRING"}},
        },
    },
    {
        "name": "web_search",
        "description": "Real-time public web research using Google Search grounding. Can target public pages on Pinterest, Instagram, Telegram, GitHub, Reddit, YouTube, etc. Never bypass login/private content.",
        "parameters": {
            "type": "OBJECT",
            "required": ["query"],
            "properties": {
                "query": {"type": "STRING"},
                "platform": {"type": "STRING", "description": "Optional platform/domain hint"},
            },
        },
    },
    {
        "name": "fetch_url",
        "description": "Fetch and extract text from a public HTTPS URL.",
        "parameters": {
            "type": "OBJECT",
            "required": ["url"],
            "properties": {"url": {"type": "STRING"}},
        },
    },
    {
        "name": "download_media",
        "description": "Download a public HTTPS image/file to a temporary server path so it can be sent to Telegram.",
        "parameters": {
            "type": "OBJECT",
            "required": ["url"],
            "properties": {"url": {"type": "STRING"}},
        },
    },
    {
        "name": "send_message",
        "description": "Send a Telegram message as the owner via MTProto. Use only when the owner explicitly requests it.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer", "text"],
            "properties": {
                "peer": {"type": "STRING"},
                "text": {"type": "STRING"},
                "reply_to": {"type": "INTEGER"},
            },
        },
    },
    {
        "name": "edit_message",
        "description": "Edit one Telegram message sent by the owner.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer", "message_id", "text"],
            "properties": {"peer": {"type": "STRING"}, "message_id": {"type": "INTEGER"}, "text": {"type": "STRING"}},
        },
    },
    {
        "name": "delete_messages",
        "description": "Delete Telegram messages. High-impact and confirmation-gated.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer", "message_ids"],
            "properties": {
                "peer": {"type": "STRING"},
                "message_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                "revoke": {"type": "BOOLEAN"},
            },
        },
    },
    {
        "name": "forward_message",
        "description": "Forward one Telegram message from source to target.",
        "parameters": {
            "type": "OBJECT",
            "required": ["target", "source", "message_id"],
            "properties": {"target": {"type": "STRING"}, "source": {"type": "STRING"}, "message_id": {"type": "INTEGER"}},
        },
    },
    {
        "name": "pin_message",
        "description": "Pin a Telegram message in a chat.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer", "message_id"],
            "properties": {"peer": {"type": "STRING"}, "message_id": {"type": "INTEGER"}, "notify": {"type": "BOOLEAN"}},
        },
    },
    {
        "name": "unpin_message",
        "description": "Unpin a Telegram message or current pin.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer"],
            "properties": {"peer": {"type": "STRING"}, "message_id": {"type": "INTEGER"}},
        },
    },
    {
        "name": "mark_read",
        "description": "Mark a Telegram chat read.",
        "parameters": {"type": "OBJECT", "required": ["peer"], "properties": {"peer": {"type": "STRING"}}},
    },
    {
        "name": "archive_chat",
        "description": "Archive or unarchive a Telegram chat.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer"],
            "properties": {"peer": {"type": "STRING"}, "archived": {"type": "BOOLEAN"}},
        },
    },
    {
        "name": "mute_chat",
        "description": "Mute one Telegram chat for N minutes.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer"],
            "properties": {"peer": {"type": "STRING"}, "minutes": {"type": "INTEGER"}},
        },
    },
    {
        "name": "unmute_chat",
        "description": "Unmute one Telegram chat.",
        "parameters": {"type": "OBJECT", "required": ["peer"], "properties": {"peer": {"type": "STRING"}}},
    },
    {
        "name": "block_user",
        "description": "Block a Telegram user. Confirmation-gated.",
        "parameters": {"type": "OBJECT", "required": ["peer"], "properties": {"peer": {"type": "STRING"}}},
    },
    {
        "name": "unblock_user",
        "description": "Unblock a Telegram user. Confirmation-gated.",
        "parameters": {"type": "OBJECT", "required": ["peer"], "properties": {"peer": {"type": "STRING"}}},
    },
    {
        "name": "add_contact",
        "description": "Add an existing Telegram user as a contact.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer", "first_name"],
            "properties": {
                "peer": {"type": "STRING"},
                "first_name": {"type": "STRING"},
                "last_name": {"type": "STRING"},
                "phone": {"type": "STRING"},
            },
        },
    },
    {
        "name": "import_contact",
        "description": "Add/import a Telegram contact by phone number through the owner user account.",
        "parameters": {
            "type": "OBJECT",
            "required": ["phone", "first_name"],
            "properties": {"phone": {"type": "STRING"}, "first_name": {"type": "STRING"}, "last_name": {"type": "STRING"}},
        },
    },
    {
        "name": "delete_contact",
        "description": "Delete a Telegram contact. Confirmation-gated.",
        "parameters": {"type": "OBJECT", "required": ["peer"], "properties": {"peer": {"type": "STRING"}}},
    },
    {
        "name": "update_profile",
        "description": "Update owner's Telegram first name, last name, or bio. Confirmation-gated.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"first_name": {"type": "STRING"}, "last_name": {"type": "STRING"}, "about": {"type": "STRING"}},
        },
    },
    {
        "name": "set_chat_wallpaper",
        "description": "Set a custom wallpaper for a private Telegram chat using current MTProto wallpaper identifiers.",
        "parameters": {
            "type": "OBJECT",
            "required": ["peer", "wallpaper_id", "access_hash"],
            "properties": {
                "peer": {"type": "STRING"},
                "wallpaper_id": {"type": "INTEGER"},
                "access_hash": {"type": "INTEGER"},
                "for_both": {"type": "BOOLEAN"},
            },
        },
    },
]

TOOL_DECLARATIONS.extend(EXTENDED_DECLARATIONS)
