"""WhatsApp transport tests against a mock bridge (no real sidecar/binary).

Covers the documented REST flow: health -> register-callback -> send, plus the
OctoOps-side /incoming callback acknowledgement.
"""

import json
import socket

import aiohttp
import pytest
from aiohttp import web

from octoops.shared.models import Response
from octoops.transports.whatsapp.bridge_client import BridgeClient
from octoops.transports.whatsapp.adapter import WhatsAppTransport

_SAMPLE_GROUPS = [
    {"jid": "111@g.us", "name": "Ops Team", "participants": 5},
    {"jid": "222@g.us", "name": "Alerts", "participants": 3},
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
async def mock_bridge():
    """A stand-in for the Whatsmeow bridge; records the calls it receives."""
    calls = {"send": [], "register": [], "health": 0, "shutdown": 0}

    async def send(request):
        calls["send"].append(await request.json())
        return web.json_response({"ok": True})

    async def health(request):
        calls["health"] += 1
        return web.json_response({"ok": True, "logged_in": True})

    async def register(request):
        calls["register"].append(await request.json())
        return web.json_response({"ok": True})

    async def shutdown(request):
        calls["shutdown"] += 1
        return web.json_response({"ok": True})

    async def groups(request):
        return web.json_response({"ok": True, "groups": _SAMPLE_GROUPS})

    app = web.Application()
    app.router.add_post("/send", send)
    app.router.add_get("/health", health)
    app.router.add_get("/groups", groups)
    app.router.add_post("/register-callback", register)
    app.router.add_post("/shutdown", shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield f"http://127.0.0.1:{port}", calls
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_bridge_client_get_groups(mock_bridge):
    base_url, _ = mock_bridge
    client = BridgeClient(base_url)
    try:
        groups = await client.get_groups()
    finally:
        await client.close()
    assert len(groups) == 2
    assert groups[0]["jid"] == "111@g.us"
    assert groups[1]["name"] == "Alerts"


@pytest.mark.asyncio
async def test_refresh_groups_updates_registry_and_file(mock_bridge, tmp_path):
    from types import SimpleNamespace
    base_url, _ = mock_bridge
    client = BridgeClient(base_url)

    paths = SimpleNamespace(data=tmp_path)
    registry = SimpleNamespace(whatsapp_groups=None, paths=paths)
    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=0,
        client=client, spawn=False,
    )
    transport._registry = registry  # type: ignore[assignment]
    transport._running = True

    await transport._refresh_groups()
    await client.close()

    assert registry.whatsapp_groups == _SAMPLE_GROUPS
    saved = json.loads((tmp_path / "whatsapp_groups.json").read_text())
    assert saved == _SAMPLE_GROUPS


@pytest.mark.asyncio
async def test_refresh_groups_skips_when_not_logged_in(tmp_path):
    class _NotLoggedInClient:
        async def health(self):
            return {"ok": True, "logged_in": False}
        async def close(self):
            pass

    from types import SimpleNamespace
    registry = SimpleNamespace(whatsapp_groups=None, paths=SimpleNamespace(data=tmp_path))
    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=0,
        client=_NotLoggedInClient(), spawn=False,
    )
    transport._registry = registry  # type: ignore[assignment]

    await transport._refresh_groups()

    assert registry.whatsapp_groups is None  # unchanged
    assert not (tmp_path / "whatsapp_groups.json").exists()


@pytest.mark.asyncio
async def test_bridge_client_roundtrip(mock_bridge):
    base_url, calls = mock_bridge
    client = BridgeClient(base_url)
    try:
        assert (await client.health())["logged_in"] is True
        await client.send("group@g.us", "hello")
        await client.register_callback("http://127.0.0.1:3001/incoming")
        await client.shutdown()
    finally:
        await client.close()

    assert calls["health"] == 1
    assert calls["send"] == [{"chat_id": "group@g.us", "text": "hello"}]
    assert calls["register"][0]["url"].endswith("/incoming")
    assert calls["shutdown"] == 1


@pytest.mark.asyncio
async def test_send_targets_whatsapp_chat_ids(mock_bridge):
    base_url, calls = mock_bridge
    client = BridgeClient(base_url)
    transport = WhatsAppTransport(
        bridge_path="/nonexistent",
        bridge_port=0,
        callback_port=0,
        client=client,
        spawn=False,
    )
    resp = Response(
        text="alert", chat_id="tg-chat", whatsapp_chat_ids=["a@g.us", "b@g.us"]
    )
    await transport.send(resp)
    await client.close()

    assert {c["chat_id"] for c in calls["send"]} == {"a@g.us", "b@g.us"}
    assert all(c["text"] == "alert" for c in calls["send"])


@pytest.mark.asyncio
async def test_send_no_targets_is_noop(mock_bridge):
    base_url, calls = mock_bridge
    client = BridgeClient(base_url)
    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=0,
        client=client, spawn=False,
    )
    await transport.send(Response(text="x", chat_id="c"))  # no whatsapp_chat_ids
    await client.close()
    assert calls["send"] == []


@pytest.mark.asyncio
async def test_health_and_register_flow(mock_bridge):
    base_url, calls = mock_bridge
    client = BridgeClient(base_url)
    cb_port = _free_port()
    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=cb_port,
        client=client, spawn=False,
    )
    transport._running = True
    assert await transport._await_health() is True
    await transport._register_callback()
    await client.close()

    assert calls["health"] >= 1
    assert calls["register"][0]["url"] == f"http://127.0.0.1:{cb_port}/incoming"


class _FakeRouter:
    """Minimal router exposing entries() for the access-line tests."""

    def __init__(self, entries):
        self._entries = entries  # list of (name, command_def, module_name)

    def entries(self):
        return self._entries


def _access_transport(*, inbound, command="vencimentos", router=None):
    t = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=0,
        spawn=False, inbound_enabled=inbound, command=command,
    )
    t._router = router
    return t


def test_access_text_output_only_when_inbound_off():
    t = _access_transport(inbound=False)
    assert "output-only" in t._whatsapp_access_text("en")
    assert "somente saída" in t._whatsapp_access_text("pt-BR")


def test_access_text_names_command_and_module():
    router = _FakeRouter([("vencimentos", None, "deadlines")])
    t = _access_transport(inbound=True, command="vencimentos", router=router)
    en = t._whatsapp_access_text("en")
    assert "/vencimentos" in en and "(deadlines)" in en and "send a message" in en
    pt = t._whatsapp_access_text("pt-BR")
    assert "/vencimentos" in pt and "(deadlines)" in pt and "envie uma mensagem" in pt


def test_access_text_warns_when_command_not_registered():
    # The classic whatsapp_command / core.language mismatch: "deadlines" set but
    # only "vencimentos" is registered -> the line flags it.
    router = _FakeRouter([("vencimentos", None, "deadlines")])
    t = _access_transport(inbound=True, command="deadlines", router=router)
    en = t._whatsapp_access_text("en")
    assert "not " in en and "/deadlines" in en and "whatsapp_command" in en


@pytest.mark.asyncio
async def test_notify_admins_appends_access_line(registry):
    sent = []

    class _Client:
        async def send(self, chat_id, text):
            sent.append((chat_id, text))

        async def close(self):
            pass

    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=0,
        client=_Client(), spawn=False, admin_chat_ids=["5511999998888@s.whatsapp.net"],
    )
    transport._registry = registry  # inbound off by default
    await transport._notify_admins()

    assert len(sent) == 1
    body = sent[0][1]
    assert "OctoOps started" in body
    assert "WhatsApp: output-only" in body  # the new access line


@pytest.mark.asyncio
async def test_incoming_callback_acks_without_routing():
    cb_port = _free_port()
    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=cb_port, spawn=False
    )
    await transport._start_callback_server()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{cb_port}/incoming",
                json={"chat_id": "x", "text": "ignored"},
            ) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body == {"ok": True, "routed": False}
    finally:
        await transport._runner.cleanup()
