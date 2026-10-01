from app.security.redaction import redact_mapping, redact_text


def test_redacts_gemini_auth_key():
    sample = "AQ." + "A" * 40
    assert sample not in redact_text(f"key={sample}")
    assert "[REDACTED]" in redact_text(f"key={sample}")


def test_mapping_redacts_sensitive_keys():
    out = redact_mapping({"telegram_session": "abc", "safe": "hello"})
    assert out["telegram_session"] == "[REDACTED]"
    assert out["safe"] == "hello"
