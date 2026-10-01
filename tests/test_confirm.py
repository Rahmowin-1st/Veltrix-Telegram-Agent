from app.tools.confirm import ConfirmationManager


def test_confirmation_roundtrip():
    manager = ConfirmationManager(ttl_seconds=30)
    pending = manager.create(123, "delete_messages", {"peer": "x", "message_ids": [1]})
    assert manager.consume(999, pending.token) is None
    consumed = manager.consume(123, pending.token)
    assert consumed is not None
    assert consumed.action == "delete_messages"
    assert manager.consume(123, pending.token) is None
