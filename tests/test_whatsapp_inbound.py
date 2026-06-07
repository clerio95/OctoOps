"""WhatsApp brain-only inbound: the /incoming routing path and its config.

No bridge/network: a fake BridgeClient captures replies and a fake Router
captures dispatches. Inbound is off by default (output-only), so the existing
ack-only behavior is preserved.
"""

from __future__ import annotations

from octoops.core.config import AppConfig
from octoops.core.errors import ConfigError
from octoops.shared.models import Response, Role, TransportSource
from octoops.transports.whatsapp.adapter import (
    WhatsAppTransport,
    _extract,
    normalize_number,
)


# --- helpers -----------------------------------------------------------------


class _FakeReq:
    def __init__(self, payload, raise_json=False):
        self._payload = payload
        self._raise = raise_json

    async def json(self):
        if self._raise:
            raise ValueError("bad body")
        return self._payload


class _FakeRouter:
    def __init__(self, response=None, has=True):
        self._response = response
        self._has = has
        self.dispatched = []

    def has_command(self, name):
        return self._has

    async def dispatch(self, req, *, role_override=None):
        self.dispatched.append((req, role_override))
        return self._response


class _FakeClient:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"ok": True}

    async def close(self):
        pass


def _transport(*, inbound, allow, router, response=None, has=True, command="ask"):
    client = _FakeClient()
    t = WhatsAppTransport(
        bridge_path="/nonexistent",
        bridge_port=0,
        callback_port=0,
        client=client,
        spawn=False,
        inbound_enabled=inbound,
        allow=allow,
        command=command,
        role=Role.Operator,
    )
    t._router = router
    return t, client


async def _body(resp):
    # web.json_response stores the serialized text; parse it back.
    import json

    return json.loads(resp.text)


# --- pure helpers ------------------------------------------------------------


def test_normalize_number_strips_jid_and_punctuation():
    assert normalize_number("5511999998888@s.whatsapp.net") == "5511999998888"
    assert normalize_number("+55 11 99999-8888") == "5511999998888"


def test_normalize_number_no_digits():
    assert normalize_number("group@g.us") == ""


def test_extract_picks_first_present_key():
    assert _extract({"sender": "x", "from": "y"}, ("from", "sender")) == "y"
    assert _extract({"body": " hi "}, ("text", "body")) == "hi"
    assert _extract({}, ("text",)) is None
    assert _extract("nope", ("text",)) is None


# --- inbound disabled (default = output-only) --------------------------------


async def test_inbound_disabled_acks_without_routing():
    router = _FakeRouter(response=Response(text="should not happen", chat_id="c"))
    t, client = _transport(inbound=False, allow=["5511999998888"], router=router)
    resp = await t._handle_incoming(
        _FakeReq({"from": "5511999998888@s.whatsapp.net", "text": "hi"})
    )
    assert await _body(resp) == {"ok": True, "routed": False}
    assert router.dispatched == []
    assert client.sent == []


async def test_inbound_enabled_but_command_missing_acks():
    router = _FakeRouter(has=False)
    t, client = _transport(inbound=True, allow=["5511999998888"], router=router)
    resp = await t._handle_incoming(
        _FakeReq({"from": "5511999998888@s.whatsapp.net", "text": "hi"})
    )
    assert await _body(resp) == {"ok": True, "routed": False}
    assert router.dispatched == []


# --- inbound enabled ---------------------------------------------------------


async def test_allowlisted_sender_is_routed_and_replied():
    router = _FakeRouter(response=Response(text="42", chat_id="ignored"))
    t, client = _transport(inbound=True, allow=["+55 11 99999-8888"], router=router)
    jid = "5511999998888@s.whatsapp.net"
    resp = await t._handle_incoming(_FakeReq({"from": jid, "text": "meaning of life?"}))

    assert await _body(resp) == {"ok": True, "routed": True}
    # Dispatched exactly once, as the forced command, at the configured role.
    assert len(router.dispatched) == 1
    req, role_override = router.dispatched[0]
    assert req.command == "ask"
    assert req.args == ["meaning of life?"]
    assert req.source is TransportSource.WhatsApp
    assert req.user_id == "5511999998888"  # normalized
    assert role_override is Role.Operator
    # Reply goes back to the original JID.
    assert client.sent == [(jid, "42")]


async def test_non_allowlisted_sender_dropped_silently():
    router = _FakeRouter(response=Response(text="nope", chat_id="c"))
    t, client = _transport(inbound=True, allow=["5511999998888"], router=router)
    resp = await t._handle_incoming(
        _FakeReq({"from": "5519888887777@s.whatsapp.net", "text": "let me in"})
    )
    assert await _body(resp) == {"ok": True, "routed": False}
    assert router.dispatched == []
    assert client.sent == []


async def test_command_is_forced_even_if_text_looks_like_a_command():
    """A WhatsApp user typing '/grant ...' still only reaches the brain."""
    router = _FakeRouter(response=Response(text="ok", chat_id="c"))
    t, client = _transport(inbound=True, allow=["5511999998888"], router=router)
    await t._handle_incoming(
        _FakeReq({"from": "5511999998888@s.whatsapp.net", "text": "/grant admin 999"})
    )
    req, _ = router.dispatched[0]
    assert req.command == "ask"                      # NOT 'grant'
    assert req.args == ["/grant admin 999"]          # the whole text is just a question


async def test_missing_text_is_ignored():
    router = _FakeRouter(response=Response(text="x", chat_id="c"))
    t, client = _transport(inbound=True, allow=["5511999998888"], router=router)
    resp = await t._handle_incoming(_FakeReq({"from": "5511999998888@s.whatsapp.net"}))
    assert await _body(resp) == {"ok": True, "routed": False}
    assert router.dispatched == []


async def test_malformed_body_is_acked():
    router = _FakeRouter()
    t, client = _transport(inbound=True, allow=["5511999998888"], router=router)
    resp = await t._handle_incoming(_FakeReq(None, raise_json=True))
    assert await _body(resp) == {"ok": True, "routed": False}
    assert router.dispatched == []


async def test_no_reply_when_dispatch_returns_none():
    router = _FakeRouter(response=None)
    t, client = _transport(inbound=True, allow=["5511999998888"], router=router)
    resp = await t._handle_incoming(
        _FakeReq({"from": "5511999998888@s.whatsapp.net", "text": "q"})
    )
    assert await _body(resp) == {"ok": True, "routed": True}
    assert client.sent == []


# --- config parsing ----------------------------------------------------------


def _base_data(transport: dict) -> dict:
    return {
        "telegram": {"bot_token": "t", "admin_chat_id": "1"},
        "core": {"timezone": "UTC"},
        "transport": transport,
    }


def test_config_parses_inbound_fields():
    cfg = AppConfig.from_dict(
        _base_data(
            {
                "whatsapp_inbound_enabled": True,
                "whatsapp_allow": ["+55 11 99999-8888"],
                "whatsapp_command": "ask",
                "whatsapp_role": "admin",
            }
        )
    )
    assert cfg.transport.whatsapp_inbound_enabled is True
    assert cfg.transport.whatsapp_allow == ["+55 11 99999-8888"]  # raw; normalized in adapter
    assert cfg.transport.whatsapp_command == "ask"
    assert cfg.transport.whatsapp_role is Role.Admin


def test_config_inbound_defaults_off():
    cfg = AppConfig.from_dict(_base_data({}))
    assert cfg.transport.whatsapp_inbound_enabled is False
    assert cfg.transport.whatsapp_allow == []
    assert cfg.transport.whatsapp_command == "ask"
    assert cfg.transport.whatsapp_role is Role.Operator


def test_config_bad_whatsapp_role_raises():
    try:
        AppConfig.from_dict(_base_data({"whatsapp_role": "king"}))
    except ConfigError:
        return
    raise AssertionError("expected ConfigError for bad whatsapp_role")
