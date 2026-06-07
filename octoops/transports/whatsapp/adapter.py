"""WhatsAppTransport — output transport over the Whatsmeow bridge, with an
optional brain-only inbound path.

Lifecycle: start a local callback HTTP server (POST /incoming), spawn the bridge
sidecar, wait for health, register our callback URL, then supervise the sidecar
(restart with exponential backoff, cap 60s). On shutdown: POST /shutdown, wait up
to 3s, then terminate.

send() pushes text to each chat in response.whatsapp_chat_ids.

Inbound is OFF by default — /incoming is acknowledged but not routed (WhatsApp
stays output-only). When inbound is enabled, a message from a whitelisted number
is routed to exactly ONE configured command (the brain's /ask by default): the
command name is forced, so a WhatsApp user can never invoke any other command.
Non-whitelisted senders are dropped silently (the bot stays invisible to randoms,
mirroring the Telegram allowlist gate).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from aiohttp import web

from octoops.core.logging import get_logger
from octoops.shared.models import Request, Response, Role, TransportSource
from octoops.transports import Transport

from .bridge_client import BridgeClient
from .formatter import format_text

if TYPE_CHECKING:
    from octoops.core.registry import Registry
    from octoops.core.router import Router

_log = get_logger("octoops.transports.whatsapp")

_MAX_BACKOFF = 60
_HEALTH_RETRIES = 30
_HEALTH_INTERVAL = 1.0
_SHUTDOWN_TIMEOUT = 3.0

# Inbound payload field names we tolerate from the bridge (it's supplied
# separately; code to the contract but don't depend on one exact field name).
_SENDER_KEYS = ("from", "sender", "jid", "chat_id", "chat")
_TEXT_KEYS = ("text", "body", "message", "content")


def normalize_number(value: str) -> str:
    """Reduce a JID/phone to comparable digits ('5511...@s.whatsapp.net' -> '5511...')."""
    local = value.split("@", 1)[0]
    return "".join(ch for ch in local if ch.isdigit())


def _extract(body: Any, keys: Iterable[str]) -> str | None:
    if not isinstance(body, dict):
        return None
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class WhatsAppTransport(Transport):
    def __init__(
        self,
        bridge_path: str,
        bridge_port: int,
        callback_port: int,
        *,
        client: BridgeClient | None = None,
        spawn: bool = True,
        inbound_enabled: bool = False,
        allow: Iterable[str] | None = None,
        command: str = "ask",
        role: Role = Role.Operator,
    ) -> None:
        self._bridge_path = bridge_path
        self._bridge_port = bridge_port
        self._callback_port = callback_port
        self._client = client or BridgeClient(f"http://127.0.0.1:{bridge_port}")
        self._spawn = spawn
        self._running = False
        self._proc: asyncio.subprocess.Process | None = None
        self._runner: web.AppRunner | None = None
        self._supervisor: asyncio.Task | None = None
        # Inbound (brain-only) routing — off unless enabled and a router is set.
        self._inbound_enabled = inbound_enabled
        self._allow = {normalize_number(x) for x in (allow or [])}
        self._command = command
        self._role = role
        self._router: "Router | None" = None

    @property
    def name(self) -> str:
        return "whatsapp"

    async def run(self, router: "Router", registry: "Registry") -> None:
        self._running = True
        self._router = router
        try:
            await self._start_callback_server()
            if self._spawn:
                self._supervisor = asyncio.create_task(self._supervise_bridge())
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self._teardown()

    async def send(self, response: "Response") -> None:
        targets = response.whatsapp_chat_ids
        if not targets:
            return
        text = format_text(response)
        for chat_id in targets:
            try:
                await self._client.send(chat_id, text)
                _log.info("whatsapp.sent", chat_id=chat_id)
            except Exception as exc:  # noqa: BLE001 - one bad chat must not fail others
                _log.error("whatsapp.send_failed", chat_id=chat_id, error=str(exc))

    # --- callback server (OctoOps side) ---------------------------------------

    async def _start_callback_server(self) -> None:
        app = web.Application()
        app.router.add_post("/incoming", self._handle_incoming)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self._callback_port)
        await site.start()
        _log.info("whatsapp.callback_server_started", port=self._callback_port)

    _NOT_ROUTED = {"ok": True, "routed": False}

    async def _handle_incoming(self, request: web.Request) -> web.Response:
        # Default: WhatsApp is output-only. Accept and acknowledge; do not route.
        # Message bodies are never logged.
        if (
            not self._inbound_enabled
            or self._router is None
            or not self._router.has_command(self._command)
        ):
            _log.info("whatsapp.incoming_ack")
            return web.json_response(self._NOT_ROUTED)

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - tolerate malformed bodies
            return web.json_response(self._NOT_ROUTED)

        sender = _extract(body, _SENDER_KEYS)
        text = _extract(body, _TEXT_KEYS)
        if not sender or not text:
            _log.info("whatsapp.incoming_ignored", reason="missing_sender_or_text")
            return web.json_response(self._NOT_ROUTED)

        if normalize_number(sender) not in self._allow:
            # Not whitelisted -> stay invisible, like the Telegram allowlist gate.
            _log.info("whatsapp.incoming_dropped", reason="not_allowed")
            return web.json_response(self._NOT_ROUTED)

        # Force the configured command: the inbound text is ALWAYS the argument to
        # that one command, so a WhatsApp user can only ever reach the brain.
        req = Request(
            command=self._command,
            args=[text],
            raw_text=text,
            user_id=normalize_number(sender),
            chat_id=sender,
            source=TransportSource.WhatsApp,
        )
        try:
            response = await self._router.dispatch(req, role_override=self._role)
        except Exception as exc:  # noqa: BLE001 - a bad dispatch must not crash the server
            _log.error("whatsapp.dispatch_failed", error=str(exc))
            return web.json_response(self._NOT_ROUTED)

        if response is not None and response.text:
            await self.send(
                Response(
                    text=response.text, chat_id=sender, whatsapp_chat_ids=[sender]
                )
            )
        _log.info("whatsapp.incoming_routed", command=self._command)
        return web.json_response({"ok": True, "routed": True})

    # --- bridge sidecar supervision -------------------------------------------

    async def _supervise_bridge(self) -> None:
        backoff = 1
        while self._running:
            proc = await self._spawn_bridge()
            if proc is None:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue
            self._proc = proc
            if await self._await_health():
                await self._register_callback()
                backoff = 1
            await proc.wait()
            self._proc = None
            if not self._running:
                break
            _log.warning("whatsapp.bridge_exited", code=proc.returncode)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _spawn_bridge(self) -> asyncio.subprocess.Process | None:
        path = Path(self._bridge_path)
        if not path.exists():
            _log.error("whatsapp.bridge_missing", path=str(path))
            return None
        try:
            proc = await asyncio.create_subprocess_exec(str(path))
            _log.info("whatsapp.bridge_spawned", pid=proc.pid)
            return proc
        except Exception as exc:  # noqa: BLE001
            _log.error("whatsapp.spawn_failed", error=str(exc))
            return None

    async def _await_health(self) -> bool:
        for _ in range(_HEALTH_RETRIES):
            if not self._running:
                return False
            try:
                data = await self._client.health()
                if data.get("ok"):
                    _log.info("whatsapp.healthy", logged_in=data.get("logged_in"))
                    return True
            except Exception:  # noqa: BLE001 - bridge still starting
                pass
            await asyncio.sleep(_HEALTH_INTERVAL)
        _log.warning("whatsapp.health_timeout")
        return False

    async def _register_callback(self) -> None:
        url = f"http://127.0.0.1:{self._callback_port}/incoming"
        try:
            await self._client.register_callback(url)
            _log.info("whatsapp.callback_registered", url=url)
        except Exception as exc:  # noqa: BLE001
            _log.error("whatsapp.register_callback_failed", error=str(exc))

    # --- teardown -------------------------------------------------------------

    async def _teardown(self) -> None:
        self._running = False
        if self._supervisor is not None:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)

        if self._proc is not None:
            try:
                await asyncio.wait_for(self._client.shutdown(), timeout=_SHUTDOWN_TIMEOUT)
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=_SHUTDOWN_TIMEOUT)
            except Exception:  # noqa: BLE001 - timeout or wait error -> force terminate
                try:
                    self._proc.terminate()
                except ProcessLookupError:
                    pass

        await self._client.close()
        if self._runner is not None:
            await self._runner.cleanup()
        _log.info("whatsapp.stopped")
