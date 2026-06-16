"""WhatsApp transport tests against a mock bridge (no real sidecar/binary).

Covers the documented REST flow: health -> register-callback -> send, plus the
OctoOps-side /incoming callback acknowledgement.
"""

import json
import socket

import asyncio

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


# --- phone -> LID auto-resolution ------------------------------------------------


class _ResolveClient:
    """Fake bridge client driving _resolve_allow_lids: {number: lid_jid|None}."""

    def __init__(self, mapping=None, fail=None):
        self._mapping = mapping or {}
        self._fail = set(fail or ())
        self.calls = []

    async def resolve_lid(self, pn):
        self.calls.append(pn)
        if pn in self._fail:
            raise RuntimeError("bridge boom")
        if pn not in self._mapping:
            return {"ok": False, "error": "not on whatsapp"}
        return {"ok": True, "pn": f"{pn}@s.whatsapp.net", "lid": self._mapping[pn] or ""}

    async def close(self):
        pass


def _resolve_transport(client, *, allow, tmp_path, inbound=True):
    from types import SimpleNamespace

    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=0,
        client=client, spawn=False, inbound_enabled=inbound, allow=allow,
    )
    transport._registry = SimpleNamespace(paths=SimpleNamespace(data=tmp_path))
    return transport


@pytest.mark.asyncio
async def test_resolve_allow_lids_adds_and_caches(tmp_path):
    client = _ResolveClient({"5527981650032": "142013227876439@lid"})
    transport = _resolve_transport(client, allow=["5527981650032"], tmp_path=tmp_path)

    await transport._resolve_allow_lids()

    assert "142013227876439" in transport._allow  # the LID is now allowed
    assert "5527981650032" in transport._allow  # original phone number kept
    cache = json.loads((tmp_path / "whatsapp_lids.json").read_text())
    assert cache == {"5527981650032": "142013227876439"}


@pytest.mark.asyncio
async def test_resolve_allow_lids_skips_unresolved(tmp_path):
    client = _ResolveClient({})  # number not on WhatsApp / no LID
    transport = _resolve_transport(client, allow=["5511999998888"], tmp_path=tmp_path)

    await transport._resolve_allow_lids()

    assert transport._allow == {"5511999998888"}  # unchanged
    assert not (tmp_path / "whatsapp_lids.json").exists()  # nothing cached


@pytest.mark.asyncio
async def test_resolve_allow_lids_survives_client_error(tmp_path):
    client = _ResolveClient(
        {"5527981650032": "142013227876439@lid"}, fail={"5511111111111"}
    )
    transport = _resolve_transport(
        client, allow=["5511111111111", "5527981650032"], tmp_path=tmp_path
    )

    await transport._resolve_allow_lids()  # must not raise

    assert "142013227876439" in transport._allow  # the healthy lookup still applied


@pytest.mark.asyncio
async def test_resolve_allow_lids_seeds_from_cache(tmp_path):
    (tmp_path / "whatsapp_lids.json").write_text(
        json.dumps({"5527981650032": "142013227876439"})
    )
    client = _ResolveClient({}, fail={"5527981650032"})  # live resolve fails
    transport = _resolve_transport(client, allow=["5527981650032"], tmp_path=tmp_path)

    await transport._resolve_allow_lids()

    assert "142013227876439" in transport._allow  # served from cache despite failure


@pytest.mark.asyncio
async def test_resolve_allow_lids_noop_when_inbound_off(tmp_path):
    client = _ResolveClient({"5527981650032": "142013227876439@lid"})
    transport = _resolve_transport(
        client, allow=["5527981650032"], tmp_path=tmp_path, inbound=False
    )

    await transport._resolve_allow_lids()

    assert client.calls == []  # never queried the bridge
    assert "142013227876439" not in transport._allow


def test_learn_inbound_lid_binds_allowlisted_phone(tmp_path):
    # Allowlisted by phone; a message arrives under an unseen LID + the phone.
    transport = _resolve_transport(None, allow=["5527981650032"], tmp_path=tmp_path)

    transport._learn_inbound_lid("142013227876439@lid", "5527981650032@s.whatsapp.net")

    assert "142013227876439" in transport._allow  # LID now allowed
    cache = json.loads((tmp_path / "whatsapp_lids.json").read_text())
    assert cache == {"5527981650032": "142013227876439"}


def test_learn_inbound_lid_ignores_unallowlisted_phone(tmp_path):
    # A phone we never allowlisted must not be learned (security boundary).
    transport = _resolve_transport(None, allow=["5527981650032"], tmp_path=tmp_path)

    transport._learn_inbound_lid("999@lid", "5511000000000@s.whatsapp.net")

    assert "999" not in transport._allow
    assert not (tmp_path / "whatsapp_lids.json").exists()


def test_learn_inbound_lid_skips_when_phone_echoes_lid(tmp_path):
    # Bridge couldn't resolve the PN and echoed the LID -> nothing to learn.
    transport = _resolve_transport(None, allow=["87188003913891"], tmp_path=tmp_path)

    transport._learn_inbound_lid("87188003913891@lid", "87188003913891")

    assert not (tmp_path / "whatsapp_lids.json").exists()  # no spurious binding


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


@pytest.mark.asyncio
async def test_notify_admins_sends_online_message(registry):
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
    transport._registry = registry
    await transport._notify_admins()

    assert len(sent) == 1
    body = sent[0][1]
    # Slim two-line online message pointing at /help — no uptime / module dump.
    assert "OctoOps Online" in body
    assert "/help" in body
    assert "Uptime" not in body and "Modules" not in body


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


# --- unpaired-session visibility -------------------------------------------------


class _HealthClient:
    """Fake bridge client whose logged_in flag the test controls."""

    def __init__(self, logged_in=False):
        self.logged_in = logged_in

    async def health(self):
        return {"ok": True, "logged_in": self.logged_in}

    async def close(self):
        pass


class _FakeTelegram:
    def __init__(self, fail_times=0):
        self.sent = []
        self._fail_times = fail_times

    async def send(self, response):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("not started")
        self.sent.append(response)


class _DoneProc:
    returncode = None


@pytest.mark.asyncio
async def test_wait_logged_in_immediate_when_paired(registry):
    transport = WhatsAppTransport(
        "/nonexistent", 0, 0, client=_HealthClient(logged_in=True), spawn=False
    )
    transport._registry = registry
    transport._running = True
    assert await transport._wait_logged_in(_DoneProc()) is True


@pytest.mark.asyncio
async def test_unpaired_notifies_telegram_admin_once(registry, monkeypatch):
    """Bridge healthy but no session -> the Telegram admin hears about it, once."""
    import octoops.transports.whatsapp.adapter as adapter_mod

    monkeypatch.setattr(adapter_mod, "_PAIR_POLL_INTERVAL", 0.01)
    client = _HealthClient(logged_in=False)
    telegram = _FakeTelegram()
    registry.transports["telegram"] = telegram

    transport = WhatsAppTransport("/nonexistent", 0, 0, client=client, spawn=False)
    transport._registry = registry
    transport._running = True

    async def flip_after_delay():
        await asyncio.sleep(0.05)
        client.logged_in = True

    flip = asyncio.ensure_future(flip_after_delay())
    assert await transport._wait_logged_in(_DoneProc()) is True
    await flip

    assert len(telegram.sent) == 1  # notified exactly once across several polls
    assert "not paired" in telegram.sent[0].text
    assert "--setup" in telegram.sent[0].text


@pytest.mark.asyncio
async def test_unpaired_notify_retries_until_telegram_is_up(registry, monkeypatch):
    """Telegram not started on the first poll -> retried, not lost."""
    import octoops.transports.whatsapp.adapter as adapter_mod

    monkeypatch.setattr(adapter_mod, "_PAIR_POLL_INTERVAL", 0.01)
    client = _HealthClient(logged_in=False)
    telegram = _FakeTelegram(fail_times=2)  # first sends raise
    registry.transports["telegram"] = telegram

    transport = WhatsAppTransport("/nonexistent", 0, 0, client=client, spawn=False)
    transport._registry = registry
    transport._running = True

    async def flip_after_delay():
        await asyncio.sleep(0.08)
        client.logged_in = True

    flip = asyncio.ensure_future(flip_after_delay())
    assert await transport._wait_logged_in(_DoneProc()) is True
    await flip
    assert len(telegram.sent) == 1  # eventually delivered despite early failures


@pytest.mark.asyncio
async def test_unpaired_notice_is_localized(registry):
    registry.config.core.language = "pt-BR"
    telegram = _FakeTelegram()
    registry.transports["telegram"] = telegram
    transport = WhatsAppTransport("/nonexistent", 0, 0, spawn=False)
    transport._registry = registry
    assert await transport._notify_unpaired() is True
    assert "não está pareado" in telegram.sent[0].text


# --- auto-update on outdated (error 405) ----------------------------------------


class _OutdatedClient:
    """Bridge client reporting outdated until the test clears the flag."""

    def __init__(self):
        self.outdated = True
        self.logged_in = False

    async def health(self):
        return {"ok": True, "logged_in": self.logged_in, "outdated": self.outdated}

    async def shutdown(self):
        pass

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_outdated_triggers_auto_rebuild(registry, monkeypatch):
    """health.outdated -> rebuild attempted, _wait_logged_in stops, admin alerted."""
    telegram = _FakeTelegram()
    registry.transports["telegram"] = telegram
    transport = WhatsAppTransport(
        "/nonexistent", 0, 0, client=_OutdatedClient(), spawn=False
    )
    transport._registry = registry
    transport._running = True

    calls = []

    async def fake_rebuild(proc):
        calls.append(proc)
        return True

    monkeypatch.setattr(transport, "_rebuild_bridge", fake_rebuild)

    # outdated -> _handle_outdated returns True -> _wait_logged_in returns False
    assert await transport._wait_logged_in(_DoneProc()) is False
    assert len(calls) == 1
    assert transport._last_rebuild_at is not None
    # "updating…" then "updated. Reconnecting…"
    assert len(telegram.sent) == 2
    assert "outdated" in telegram.sent[0].text.lower()
    assert "updated" in telegram.sent[1].text.lower()


@pytest.mark.asyncio
async def test_outdated_cooldown_skips_repeated_rebuild(registry, monkeypatch):
    """Within the cooldown a second outdated event must NOT rebuild again."""
    telegram = _FakeTelegram()
    registry.transports["telegram"] = telegram
    transport = WhatsAppTransport("/nonexistent", 0, 0, spawn=False)
    transport._registry = registry

    rebuilds = []

    async def fake_rebuild(proc):
        rebuilds.append(proc)
        return False  # rebuilt bridge still rejected

    monkeypatch.setattr(transport, "_rebuild_bridge", fake_rebuild)

    # First outdated event: rebuild attempted (and fails -> manual fallback alert).
    assert await transport._handle_outdated(_DoneProc()) is True
    assert len(rebuilds) == 1
    # Second event while cooling down: skipped, no extra rebuild.
    assert await transport._handle_outdated(_DoneProc()) is False
    assert len(rebuilds) == 1
    # Manual-steps alert sent once for the cooldown window, not per poll.
    cooldown_alerts = [m for m in telegram.sent if "go get" in m.text]
    assert len(cooldown_alerts) >= 1


@pytest.mark.asyncio
async def test_rebuild_aborts_when_go_missing(registry, monkeypatch):
    import octoops.transports.whatsapp.adapter as adapter_mod

    monkeypatch.setattr(adapter_mod.shutil, "which", lambda _name: None)
    transport = WhatsAppTransport(
        "/nonexistent", 0, 0, client=_OutdatedClient(), spawn=False
    )
    transport._registry = registry
    # No source dir resolvable + no go -> returns False without touching the proc.
    assert await transport._rebuild_bridge(_DoneProc()) is False


@pytest.mark.asyncio
async def test_outdated_alert_is_localized(registry, monkeypatch):
    registry.config.core.language = "pt-BR"
    telegram = _FakeTelegram()
    registry.transports["telegram"] = telegram
    transport = WhatsAppTransport("/nonexistent", 0, 0, spawn=False)
    transport._registry = registry

    async def fake_rebuild(proc):
        return True

    monkeypatch.setattr(transport, "_rebuild_bridge", fake_rebuild)
    await transport._handle_outdated(_DoneProc())
    assert "desatualizada" in telegram.sent[0].text


def test_bridge_source_dir_and_output_path(registry, tmp_path):
    from octoops.core.paths import AppPaths

    (tmp_path / "whatsmeow-bridge").mkdir()
    (tmp_path / "whatsmeow-bridge" / "main.go").write_text("package main\n")
    registry.paths = AppPaths(home=tmp_path)
    transport = WhatsAppTransport("./whatsmeow-bridge.exe", 0, 0, spawn=False)
    transport._registry = registry
    assert transport._bridge_source_dir() == tmp_path / "whatsmeow-bridge"
    # Relative exe path resolves against home, not the test's CWD.
    assert transport._bridge_output_path() == tmp_path / "whatsmeow-bridge.exe"
