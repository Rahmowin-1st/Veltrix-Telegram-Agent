from app.security.policy import evaluate_action


def test_delete_requires_confirmation():
    d = evaluate_action("delete_messages", explicit_current_request=True, require_confirmation=True)
    assert d.allowed
    assert d.requires_confirmation


def test_send_explicit_does_not_require_confirmation():
    d = evaluate_action("send_message", explicit_current_request=True, require_confirmation=True)
    assert d.allowed
    assert not d.requires_confirmation
