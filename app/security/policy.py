from __future__ import annotations

from dataclasses import dataclass

HIGH_IMPACT_ACTIONS = {
    "delete_messages",
    "bulk_delete_messages",
    "block_user",
    "unblock_user",
    "delete_contact",
    "set_global_privacy",
    "update_profile",
    "set_profile_photo",
    "mass_send",
    "terminate_session",
    "leave_channel",
    "create_channel",
    "edit_channel_info",
    "set_channel_admin",
    "ban_channel_member",
    "uninstall_sticker_set",
    "create_sticker_set",
    "add_sticker_to_set",
}


@dataclass(slots=True)
class ActionDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str = ""


def evaluate_action(
    action: str, *, explicit_current_request: bool, require_confirmation: bool
) -> ActionDecision:
    if action in {"mass_send", "terminate_session"}:
        return ActionDecision(True, True, "High-impact action requires explicit confirmation.")
    if action in HIGH_IMPACT_ACTIONS:
        return ActionDecision(True, True, "Account-changing action requires explicit confirmation.")
    if action.startswith("send_") and not explicit_current_request:
        return ActionDecision(True, require_confirmation, "Unsolicited writes should be confirmed.")
    return ActionDecision(True, False, "")
