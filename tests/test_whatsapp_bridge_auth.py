"""Shared-token auth on the local bridge link (both directions).

Closes the unauthenticated-bridge gap: OctoOps→bridge requests carry a bearer
token (BridgeClient), and the bridge→/incoming callback is rejected unless it
presents that same per-process token.
"""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web

from octoops.transports.whatsapp.adapter import WhatsAppTransport
from octoops.transports.whatsapp.bridge_client import BridgeClient


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- BridgeClient: presents the bearer token outbound -------------------------


def test_bridge_client_headers_include_token_only_when_set():
    assert BridgeClient("http://x")._headers() is None
    c = BridgeClient("http://x", token="abc")
    assert c._headers() == {"Authorization": "Bearer abc"}
    c.set_auth_token(None)
    assert c._headers() is None


@pytest.mark.asyncio
async def test_bridge_client_sends_authorization_header_over_the_wire():
    seen: dict[str, str | None] = {}

    async def send(request):
        seen["auth"] = request.headers.get("Authorization")
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/send", send)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    try:
        client = BridgeClient(f"http://127.0.0.1:{port}", token="s3cret")
        await client.send("a@g.us", "hi")
        await client.close()
    finally:
        await runner.cleanup()

    assert seen["auth"] == "Bearer s3cret"


# --- /incoming: rejects callers without the shared token ----------------------


@pytest.mark.asyncio
async def test_incoming_rejects_missing_or_wrong_token_and_accepts_correct():
    cb_port = _free_port()
    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=cb_port, spawn=False
    )
    # Simulate what run() does: arm the per-process shared token.
    transport._bridge_token = "the-shared-token"
    await transport._start_callback_server()
    url = f"http://127.0.0.1:{cb_port}/incoming"
    try:
        async with aiohttp.ClientSession() as session:
            # No Authorization header -> 401.
            async with session.post(url, json={"text": "x"}) as resp:
                assert resp.status == 401
            # Wrong token -> 401.
            async with session.post(
                url, json={"text": "x"}, headers={"Authorization": "Bearer nope"}
            ) as resp:
                assert resp.status == 401
            # Correct token -> 200 (inbound disabled, so acked-not-routed).
            async with session.post(
                url,
                json={"text": "x"},
                headers={"Authorization": "Bearer the-shared-token"},
            ) as resp:
                assert resp.status == 200
                assert await resp.json() == {"ok": True, "routed": False}
    finally:
        await transport._runner.cleanup()


@pytest.mark.asyncio
async def test_incoming_unauthenticated_when_no_token_set():
    # Backward-compat / pre-token bridge: with no token armed, /incoming behaves
    # exactly as before (the existing ack path), so direct callers still work.
    cb_port = _free_port()
    transport = WhatsAppTransport(
        bridge_path="/nonexistent", bridge_port=0, callback_port=cb_port, spawn=False
    )
    assert transport._bridge_token == ""  # not armed
    await transport._start_callback_server()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{cb_port}/incoming", json={"text": "x"}
            ) as resp:
                assert resp.status == 200
                assert await resp.json() == {"ok": True, "routed": False}
    finally:
        await transport._runner.cleanup()


# --- bridge subprocess env hygiene ---------------------------------------------
# The parent env can hold module secrets (.env is loaded into os.environ); the
# bridge must receive an allowlisted minimum, never the full environment.


def test_bridge_env_excludes_unrelated_secrets(monkeypatch):
    from octoops.transports.whatsapp.bridge_client import bridge_env

    monkeypatch.setenv("BRAIN_API_KEY", "sk-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = bridge_env(token="tok", port=3000)
    assert "BRAIN_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["BRIDGE_TOKEN"] == "tok"
    assert env["BRIDGE_PORT"] == "3000"


def test_bridge_env_omits_bridge_vars_when_unset():
    from octoops.transports.whatsapp.bridge_client import bridge_env

    env = bridge_env()
    assert "BRIDGE_TOKEN" not in env
    assert "BRIDGE_PORT" not in env


def test_bridge_env_allowlist_is_case_insensitive(monkeypatch):
    from octoops.transports.whatsapp.bridge_client import bridge_env

    monkeypatch.setenv("http_proxy", "http://proxy:8080")  # Go honors lowercase
    env = bridge_env()
    assert env.get("http_proxy") == "http://proxy:8080"


@pytest.mark.asyncio
async def test_spawn_bridge_passes_minimal_env(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    bridge = tmp_path / "bridge"
    bridge.write_text("")
    transport = WhatsAppTransport(str(bridge), 3001, 3002, spawn=False)
    transport._bridge_token = "tok"

    monkeypatch.setenv("BRAIN_API_KEY", "sk-secret")
    seen = {}

    async def fake_exec(path, env=None):
        seen["env"] = env
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    proc = await transport._spawn_bridge()
    assert proc is not None
    assert "BRAIN_API_KEY" not in seen["env"]
    assert seen["env"]["BRIDGE_TOKEN"] == "tok"
    assert seen["env"]["BRIDGE_PORT"] == "3001"


# --- persisted token + stale-bridge reaping --------------------------------------
# Killing OctoOps doesn't kill the Go sidecar on Windows; the orphan keeps the
# port. The token is persisted per-install so the next process can authenticate
# to the orphan and shut it down instead of deadlocking against it.


def _registry_with_paths(registry, tmp_path):
    from octoops.core.paths import AppPaths

    registry.paths = AppPaths(home=tmp_path)
    return registry


def test_bridge_token_is_persisted_and_reused(registry, tmp_path):
    _registry_with_paths(registry, tmp_path)
    t1 = WhatsAppTransport("bridge", 3001, 3002)
    t1._registry = registry
    token1 = t1._ensure_bridge_token()

    t2 = WhatsAppTransport("bridge", 3001, 3002)
    t2._registry = registry
    assert t2._ensure_bridge_token() == token1  # same install -> same secret

    token_file = tmp_path / "data" / "bridge.token"
    assert token_file.read_text("utf-8").strip() == token1
    import os
    import stat

    if os.name != "nt":
        assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600


def test_bridge_token_ephemeral_without_paths():
    t1 = WhatsAppTransport("bridge", 3001, 3002)
    t2 = WhatsAppTransport("bridge", 3001, 3002)
    assert t1._ensure_bridge_token() != t2._ensure_bridge_token()


async def _orphan_bridge(port, *, status=200):
    """A fake leftover bridge: /health answers, /shutdown records the call."""
    calls = {"shutdown": 0}

    async def health(request):
        if status != 200:
            return web.json_response({"ok": False}, status=status)
        return web.json_response({"ok": True, "logged_in": True})

    async def shutdown(request):
        calls["shutdown"] += 1
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/shutdown", shutdown)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, calls


@pytest.mark.asyncio
async def test_reap_shuts_down_a_stale_bridge():
    port = _free_port()
    runner, calls = await _orphan_bridge(port)
    try:
        transport = WhatsAppTransport("bridge", port, 3002)
        transport._bridge_token = "tok"
        transport._client.set_auth_token("tok")
        await transport._reap_stale_bridge()
        await transport._client.close()
    finally:
        await runner.cleanup()
    assert calls["shutdown"] == 1


@pytest.mark.asyncio
async def test_reap_reports_foreign_bridge_without_shutdown():
    port = _free_port()
    runner, calls = await _orphan_bridge(port, status=401)  # unknown token
    try:
        transport = WhatsAppTransport("bridge", port, 3002)
        transport._bridge_token = "tok"
        transport._client.set_auth_token("tok")
        await transport._reap_stale_bridge()  # must not raise
        await transport._client.close()
    finally:
        await runner.cleanup()
    assert calls["shutdown"] == 0


@pytest.mark.asyncio
async def test_reap_is_quiet_when_nothing_listening():
    transport = WhatsAppTransport("bridge", _free_port(), 3002)
    transport._bridge_token = "tok"
    await transport._reap_stale_bridge()  # must not raise
    await transport._client.close()
