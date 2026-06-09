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
