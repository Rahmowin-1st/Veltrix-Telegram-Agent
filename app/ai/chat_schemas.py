"""Conversation controls are bound to the current private chat by the server."""

CHAT_TOOLS = frozenset(
    {
        "assistant_status",
        "assistant_capabilities",
        "memory_status",
        "set_memory",
        "forget_conversation",
        "cancel_pending_actions",
    }
)
CHAT_WRITES = frozenset({"set_memory", "forget_conversation", "cancel_pending_actions"})


def declaration(name, description, properties=None, required=None):
    parameters = {"type": "OBJECT", "properties": properties or {}}
    if required:
        parameters["required"] = required
    return {"name": name, "description": description, "parameters": parameters}


CHAT_DECLARATIONS = [
    declaration(
        "assistant_status",
        "Check AI readiness, connection and account access, without reading Telegram. Configured is NOT verified. No secrets or IDs.",
    ),
    declaration(
        "assistant_capabilities",
        "Get the supported assistant capabilities and restrictions. Explain them naturally, never as commands or JSON.",
    ),
    declaration(
        "memory_status", "Check whether this private chat's conversation memory is enabled."
    ),
    declaration(
        "set_memory",
        "Enable/disable this private chat's conversation memory only when the human requests it. Disabling does not erase previous memory.",
        {"enabled": {"type": "BOOLEAN"}},
        ["enabled"],
    ),
    declaration(
        "forget_conversation",
        "Erase ONLY this private chat's conversation history when the human explicitly asks to forget it. Does not delete Telegram messages or change memory preference.",
    ),
    declaration(
        "cancel_pending_actions",
        "Cancel all unexecuted confirmation previews in THIS private chat, only when the human requests cancellation. Does not undo executed actions.",
    ),
]
