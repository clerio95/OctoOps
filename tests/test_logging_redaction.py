"""Redaction: sensitive keys masked, and known secret values scrubbed anywhere."""

import octoops.core.logging as oplog


def test_sensitive_key_redacted():
    out = oplog._redact(None, "info", {"event": "x", "bot_token": "abc123"})
    assert out["bot_token"] == oplog._REDACTED


def test_message_body_key_redacted():
    out = oplog._redact(None, "info", {"event": "x", "text": "private message"})
    assert out["text"] == oplog._REDACTED


def test_secret_value_scrubbed_from_arbitrary_value():
    token = "123456:SECRET-TOKEN"
    oplog._secrets.clear()
    oplog._secrets.append(token)
    try:
        out = oplog._redact(
            None,
            "error",
            {"event": "transport.crashed", "error": f"token `{token}` rejected"},
        )
        assert token not in out["error"]
        assert oplog._REDACTED in out["error"]
    finally:
        oplog._secrets.clear()


def test_non_sensitive_values_passthrough():
    oplog._secrets.clear()
    out = oplog._redact(None, "info", {"event": "command.completed", "latency_ms": 12.0})
    assert out["latency_ms"] == 12.0
