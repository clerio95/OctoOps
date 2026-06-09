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
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from aiohttp import web

from octoops.core.logging import get_logger
from octoops.modules.status import build_status_text
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
        admin_chat_ids: Iterable[str] | None = None,
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
        # Admin JIDs to send a startup status message to once the bridge is healthy.
        self._admin_chat_ids: list[str] = list(admin_chat_ids or [])
        # Inbound (brain-only) routing — off unless enabled and a router is set.
        self._inbound_enabled = inbound_enabled
        self._allow = {normalize_number(x) for x in (allow or [])}
        self._command = command
        self._role = role
        self._router: "Router | None" = None
        self._registry: "Registry | None" = None
        # Shared secret authenticating both directions of the local bridge link
        # (OctoOps→bridge requests AND the bridge→/incoming callback). Empty until
        # run() mints it, so direct unit tests of the handlers stay unauthenticated.
        self._bridge_token: str = ""

    @property
    def name(self) -> str:
        return "whatsapp"

    async def run(self, router: "Router", registry: "Registry") -> None:
        self._running = True
        self._router = router
        self._registry = registry
        # Mint the shared bridge secret for this process and arm the client with it;
        # _spawn_bridge passes the same value to the sidecar via BRIDGE_TOKEN.
        self._bridge_token = secrets.token_urlsafe(32)
        self._client.set_auth_token(self._bridge_token)
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

    def _incoming_authorized(self, request: web.Request) -> bool:
        """True if the request carries the shared bridge token (or none is set).

        Only the real bridge knows the per-process token, so this rejects a local
        attacker POSTing a spoofed (whitelisted) sender to /incoming. When the
        token is empty (direct unit tests / pre-token bridge) auth is skipped.
        """
        if not self._bridge_token:
            return True
        expected = f"Bearer {self._bridge_token}"
        got = request.headers.get("Authorization", "")
        return hmac.compare_digest(got, expected)

    async def _handle_incoming(self, request: web.Request) -> web.Response:
        # Reject unauthenticated callers up front (even the output-only ack path),
        # so nothing local can drive or probe this endpoint by spoofing the bridge.
        if not self._incoming_authorized(request):
            _log.warning("whatsapp.incoming_unauthorized")
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

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
                await self._refresh_groups()
                await self._notify_admins()
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
            # Hand the sidecar the shared secret (and the configured port) via the
            # environment; the bridge enforces the token on every endpoint.
            env = {
                **os.environ,
                "BRIDGE_TOKEN": self._bridge_token,
                "BRIDGE_PORT": str(self._bridge_port),
            }
            proc = await asyncio.create_subprocess_exec(str(path), env=env)
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

    async def _refresh_groups(self) -> None:
        """Fetch joined groups from the bridge, cache on registry, write to data/."""
        if self._registry is None:
            return
        try:
            health = await self._client.health()
            if not health.get("logged_in"):
                _log.info("whatsapp.groups_skipped", reason="not_logged_in")
                return
            groups = await self._client.get_groups()
            self._registry.whatsapp_groups = groups
            _log.info("whatsapp.groups_fetched", count=len(groups))
            if self._registry.paths is not None:
                data_dir = self._registry.paths.data
                data_dir.mkdir(parents=True, exist_ok=True)
                path = data_dir / "whatsapp_groups.json"
                path.write_text(
                    json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                _log.info("whatsapp.groups_saved", path=str(path))
        except Exception as exc:  # noqa: BLE001 - discovery must never crash the supervisor
            _log.warning("whatsapp.groups_refresh_failed", error=str(exc))

    def _whatsapp_access_text(self, lang: str) -> str:
        """A startup-message line stating what's reachable over WhatsApp inbound.

        WhatsApp inbound is a single forced command, so this resolves that one
        command to its owning module. If inbound is off it says output-only; if
        the configured command isn't actually registered (the classic
        whatsapp_command / core.language mismatch) it warns, so the operator
        catches the misconfiguration from the startup message itself.
        """
        pt = lang.strip().lower().startswith("pt")
        if not self._inbound_enabled:
            return (
                "📵 WhatsApp: somente saída (sem comandos de entrada)."
                if pt
                else "📵 WhatsApp: output-only (no inbound commands)."
            )
        cmd = self._command.lstrip("/").lower()
        module = None
        if self._router is not None:
            for name, _cmd_def, owning_module in self._router.entries():
                if name == cmd:
                    module = owning_module
                    break
        if module is None:
            return (
                f"⚠ WhatsApp: o comando de entrada /{cmd} está ativado mas não foi "
                "registrado — verifique [transport] whatsapp_command e [core] language."
                if pt
                else f"⚠ WhatsApp: inbound command /{cmd} is enabled but not "
                "registered — check [transport] whatsapp_command and [core] language."
            )
        return (
            f"💬 WhatsApp: envie uma mensagem para usar /{cmd} ({module})."
            if pt
            else f"💬 WhatsApp: send a message to use /{cmd} ({module})."
        )

    async def _notify_admins(self) -> None:
        if not self._admin_chat_ids:
            return
        reg = self._registry
        if reg is not None:
            md = build_status_text(reg)
            # Strip markdown bold markers — WhatsApp uses its own formatting.
            text = md.replace("*OctoOps status*", "OctoOps started").replace("*", "")
            # Tell admins what's actually usable over WhatsApp (inbound is limited
            # to one forced command), localized to the configured language.
            text += "\n" + self._whatsapp_access_text(reg.config.core.language)
        else:
            text = "OctoOps started."
        try:
            await self.send(
                Response(
                    text=text,
                    chat_id=self._admin_chat_ids[0],
                    whatsapp_chat_ids=list(self._admin_chat_ids),
                )
            )
            _log.info("whatsapp.admin_notified", count=len(self._admin_chat_ids))
        except Exception as exc:  # noqa: BLE001 - best effort
            _log.warning("whatsapp.admin_notify_failed", error=str(exc))

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
