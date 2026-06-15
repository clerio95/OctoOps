"""WhatsAppTransport — output transport over the Whatsmeow bridge, with an
optional brain-only inbound path.

Lifecycle: start a local callback HTTP server (POST /incoming), spawn the bridge
sidecar, wait for health, register our callback URL, then supervise the sidecar
(restart with exponential backoff, cap 60s). On shutdown: POST /shutdown, wait up
to 3s, then terminate.

send() pushes text to each chat in response.whatsapp_chat_ids.

Inbound is OFF by default — /incoming is acknowledged but not routed (WhatsApp
stays output-only). When inbound is enabled, a message from a whitelisted number
is routed by _resolve_inbound_command: the user's open conversation first, then
a command-declared whatsapp_keyword in the first word, then the configured
default command (the brain's /ask by default). The command is never taken from
the message itself, so a WhatsApp user can't invoke arbitrary commands.
Non-whitelisted senders are dropped silently (the bot stays invisible to randoms,
mirroring the Telegram allowlist gate).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import aiohttp
from aiohttp import web

from octoops.core.conversations import conversation_key
from octoops.core.logging import get_logger
from octoops.core.secure_io import write_private_text
from octoops.modules.status import build_status_text
from octoops.shared.models import Request, Response, Role, TransportSource
from octoops.transports import Transport

from .bridge_client import BridgeClient, bridge_env
from .formatter import format_text

if TYPE_CHECKING:
    from octoops.core.registry import Registry
    from octoops.core.router import Router

_log = get_logger("octoops.transports.whatsapp")

_MAX_BACKOFF = 60
_HEALTH_RETRIES = 30
_HEALTH_INTERVAL = 1.0
_PAIR_POLL_INTERVAL = 30.0
_SHUTDOWN_TIMEOUT = 3.0

# Inbound payload field names we tolerate from the bridge (it's supplied
# separately; code to the contract but don't depend on one exact field name).
_SENDER_KEYS = ("from", "sender", "jid", "chat_id", "chat")
_SENDER_PN_KEYS = ("sender_pn", "pn", "phone")
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
        # Arm the client with the shared bridge secret; _spawn_bridge passes the
        # same value to the sidecar via BRIDGE_TOKEN.
        self._bridge_token = self._ensure_bridge_token()
        self._client.set_auth_token(self._bridge_token)
        try:
            await self._start_callback_server()
            if self._spawn:
                await self._reap_stale_bridge()
                self._supervisor = asyncio.create_task(self._supervise_bridge())
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self._teardown()

    def _ensure_bridge_token(self) -> str:
        """The shared bridge secret, persisted per-install to data/bridge.token.

        Persisting (rather than minting per process) lets a new OctoOps process
        authenticate to a bridge left running by a previous one — which is what
        makes _reap_stale_bridge able to shut an orphan down instead of
        deadlocking against it (the orphan holds the port; a per-process token
        could never match it). Ephemeral fallback when no data dir is configured.
        """
        paths = self._registry.paths if self._registry is not None else None
        if paths is None:
            return secrets.token_urlsafe(32)
        token_path = paths.data / "bridge.token"
        try:
            existing = token_path.read_text("utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
        token = secrets.token_urlsafe(32)
        try:
            write_private_text(token_path, token + "\n")
        except OSError as exc:
            _log.warning("whatsapp.bridge_token_persist_failed", error=str(exc))
        return token

    async def _reap_stale_bridge(self) -> None:
        """Shut down a bridge left over from a previous run before spawning ours.

        On Windows, killing OctoOps does not kill the Go sidecar; the orphan
        keeps the bridge port, so our own spawn could never bind and the
        supervisor would retry forever. The persisted token authenticates us to
        the orphan so it can be stopped cleanly; a bridge holding the port with
        an unknown token is reported loudly instead of failing silently.
        """
        try:
            await self._client.health()
        except aiohttp.ClientResponseError as exc:
            if exc.status in (401, 403):
                _log.error(
                    "whatsapp.stale_bridge_foreign",
                    port=self._bridge_port,
                    hint="a bridge with an unknown token holds the port — "
                    "kill the old whatsmeow-bridge process manually",
                )
            return
        except Exception:  # noqa: BLE001 - nothing listening; the normal case
            return
        _log.warning("whatsapp.stale_bridge_found", port=self._bridge_port)
        try:
            await self._client.shutdown()
            await asyncio.sleep(0.5)  # let the orphan release the port
            _log.info("whatsapp.stale_bridge_stopped")
        except Exception as exc:  # noqa: BLE001 - best effort
            _log.warning("whatsapp.stale_bridge_stop_failed", error=str(exc))

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
        if not self._inbound_enabled or self._router is None:
            _log.info("whatsapp.incoming_ack")
            return web.json_response(self._NOT_ROUTED)

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - tolerate malformed bodies
            return web.json_response(self._NOT_ROUTED)

        sender = _extract(body, _SENDER_KEYS)
        sender_pn = _extract(body, _SENDER_PN_KEYS)
        text = _extract(body, _TEXT_KEYS)
        if not sender or not text:
            _log.info("whatsapp.incoming_ignored", reason="missing_sender_or_text")
            return web.json_response(self._NOT_ROUTED)

        # WhatsApp may address a sender by an opaque LID instead of their phone
        # number. The bridge forwards the phone number as sender_pn when it can
        # resolve it; match the allowlist against either, so operators allowlist
        # phone numbers and a raw LID still works as a fallback.
        identities = {normalize_number(sender), normalize_number(sender_pn or "")}
        identities.discard("")
        if identities.isdisjoint(self._allow):
            # Not whitelisted -> stay invisible, like the Telegram allowlist gate.
            # Log the normalized sender + address kind (pn vs WhatsApp's opaque @lid)
            # so an allowlist that lists a phone number but receives a LID is diagnosable.
            kind = sender.split("@", 1)[1] if "@" in sender else "bare"
            _log.info(
                "whatsapp.incoming_dropped",
                reason="not_allowed",
                sender=normalize_number(sender),
                kind=kind,
            )
            return web.json_response(self._NOT_ROUTED)

        # Prefer the phone number as the stable, human-meaningful conversation id.
        user_id = normalize_number(sender_pn) if sender_pn else normalize_number(sender)
        command = self._resolve_inbound_command(user_id, text)
        if command is None:
            _log.info("whatsapp.incoming_ignored", reason="no_route")
            return web.json_response(self._NOT_ROUTED)

        # The inbound text is ALWAYS the argument to the resolved command — a
        # WhatsApp user can only ever reach a declared keyword command, their own
        # open conversation, or the configured default. Never arbitrary commands.
        req = Request(
            command=command,
            args=[text],
            raw_text=text,
            user_id=user_id,
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
        _log.info("whatsapp.incoming_routed", command=command)
        return web.json_response({"ok": True, "routed": True})

    def _resolve_inbound_command(self, user_id: str, text: str) -> str | None:
        """Which command should this inbound WhatsApp message reach?

        Priority: the user's open conversation (so multi-step flows keep all
        their replies), then a just-expired conversation (one stale-reply
        forward so the owning module can explain the timeout), then a declared
        ``whatsapp_keywords`` match on the first word, then the configured
        default command. This is what lets several interactive modules share
        WhatsApp: each declares keywords, everything else flows to the default
        (typically the brain's /ask). Returns None when nothing is routable.
        """
        router = self._router
        if router is None:
            return None
        if self._registry is not None:
            key = conversation_key(TransportSource.WhatsApp, user_id)
            conv = self._registry.conversations.get(key)
            if conv is not None and router.has_command(conv.command):
                return conv.command
            expired = self._registry.conversations.expired_command(key)
            if expired is not None and router.has_command(expired):
                return expired
        parts = text.split()
        token = parts[0].lstrip("/").lower() if parts else ""
        if token:
            for name, cmd_def, _module in router.entries():
                for keyword in getattr(cmd_def, "whatsapp_keywords", ()) or ():
                    if token == keyword.lstrip("/").lower():
                        return name
        if self._command and router.has_command(self._command):
            return self._command
        return None

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
                if await self._wait_logged_in(proc):
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
            # Hand the sidecar the shared secret (and the configured port) via a
            # minimal allowlisted environment — never the full parent env, which
            # may hold module secrets; the bridge enforces the token on every
            # endpoint.
            env = bridge_env(token=self._bridge_token, port=self._bridge_port)
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

    async def _wait_logged_in(self, proc: asyncio.subprocess.Process) -> bool:
        """Wait until the bridge session is paired; nudge the operator meanwhile.

        A healthy bridge with no WhatsApp session (first boot before pairing, a
        logged-out session) previously just sat there invisibly: the QR code went
        to a truncated stdout log, the startup notify failed, and groups were
        never fetched after a later pairing. Now: poll health until logged_in,
        tell the Telegram admin once what's wrong and how to fix it, and run the
        normal post-login steps when pairing completes.
        """
        notified = False
        while self._running and proc.returncode is None:
            try:
                health = await self._client.health()
            except Exception:  # noqa: BLE001 - bridge died/restarting; supervisor handles it
                return False
            if health.get("logged_in"):
                return True
            if not notified:
                _log.warning("whatsapp.not_paired")
                notified = await self._notify_unpaired()
            await asyncio.sleep(_PAIR_POLL_INTERVAL)
        return False

    async def _notify_unpaired(self) -> bool:
        """Tell the Telegram admin that WhatsApp needs pairing (best-effort).

        Returns True once delivered so the caller stops retrying; False when the
        Telegram transport isn't up yet (it retries on the next pairing poll).
        """
        reg = self._registry
        if reg is None:
            return True  # nothing to notify through; don't keep trying
        telegram = reg.transports.get("telegram")
        if telegram is None:
            return False
        pt = reg.config.core.language.strip().lower().startswith("pt")
        text = (
            "⚠ WhatsApp não está pareado (sem sessão ativa). Rode "
            "`python -m octoops --setup` na máquina e escaneie o QR code. "
            "O WhatsApp fica offline até lá."
            if pt
            else "⚠ WhatsApp is not paired (no active session). Run "
            "`python -m octoops --setup` on the machine and scan the QR code. "
            "WhatsApp stays offline until then."
        )
        try:
            await telegram.send(
                Response(text=text, chat_id=reg.config.telegram.admin_chat_id)
            )
            _log.info("whatsapp.unpaired_admin_notified")
            return True
        except Exception as exc:  # noqa: BLE001 - telegram may not be started yet
            _log.warning("whatsapp.unpaired_notify_failed", error=str(exc))
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
        keywords: set[str] = set()
        if self._router is not None:
            for name, cmd_def, owning_module in self._router.entries():
                if name == cmd:
                    module = owning_module
                for keyword in getattr(cmd_def, "whatsapp_keywords", ()) or ():
                    keywords.add(keyword.lstrip("/").lower())
        keyword_list = ", ".join(sorted(keywords))
        if module is None:
            if keywords:
                # No default command, but keyword-routed modules are reachable.
                return (
                    f"💬 WhatsApp: comandos por palavra-chave: {keyword_list}."
                    if pt
                    else f"💬 WhatsApp: keyword commands: {keyword_list}."
                )
            return (
                f"⚠ WhatsApp: o comando de entrada /{cmd} está ativado mas não foi "
                "registrado — verifique [transport] whatsapp_command e [core] language."
                if pt
                else f"⚠ WhatsApp: inbound command /{cmd} is enabled but not "
                "registered — check [transport] whatsapp_command and [core] language."
            )
        line = (
            f"💬 WhatsApp: envie uma mensagem para usar /{cmd} ({module})."
            if pt
            else f"💬 WhatsApp: send a message to use /{cmd} ({module})."
        )
        if keywords:
            line += (
                f" Palavras-chave: {keyword_list}."
                if pt
                else f" Keywords: {keyword_list}."
            )
        return line

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
