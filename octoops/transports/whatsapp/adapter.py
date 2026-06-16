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
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import aiohttp
from aiohttp import web

from octoops.core.conversations import conversation_key
from octoops.core.logging import get_logger
from octoops.core.secure_io import write_private_text
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
# Auto-update: when WhatsApp rejects the bridge as outdated (error 405), OctoOps
# rebuilds it from source with the Go toolchain. The cooldown stops a rebuild
# loop if the rebuilt bridge is still rejected (e.g. whatsmeow upstream is itself
# behind, or the build is a no-op). _BUILD_TIMEOUT bounds each go step.
_REBUILD_COOLDOWN = 6 * 60 * 60.0
_BUILD_TIMEOUT = 10 * 60.0
_BRIDGE_SRC_DIRNAME = "whatsmeow-bridge"

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


def _online_message(lang: str) -> str:
    """The slim 'bot online' WhatsApp notification: title + a pointer to /help.

    The command list itself lives behind /help (and /ajuda), which over WhatsApp
    shows only the commands actually reachable there.
    """
    if (lang or "").strip().lower().startswith("pt"):
        return "OctoOps Online\n/ajuda ou /help para ver os comandos disponíveis"
    return "OctoOps Online\n/help to see the commands you can use"


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
        # Live allowlist (config numbers + LIDs resolved/cached at runtime). The
        # original config entries are kept separately as the set to resolve to LIDs.
        self._allow = {normalize_number(x) for x in (allow or [])}
        self._configured_allow = frozenset(self._allow)
        self._command = command
        self._role = role
        self._router: "Router | None" = None
        self._registry: "Registry | None" = None
        # Shared secret authenticating both directions of the local bridge link
        # (OctoOps→bridge requests AND the bridge→/incoming callback). Empty until
        # run() mints it, so direct unit tests of the handlers stay unauthenticated.
        self._bridge_token: str = ""
        # Auto-update bookkeeping (see _handle_outdated). _last_rebuild_at gates
        # the cooldown; the notified flag keeps the "still outdated" alert to once
        # per cooldown window instead of every health poll.
        self._last_rebuild_at: float | None = None
        self._outdated_cooldown_notified = False

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
                # Empty sender_pn means the bridge could not resolve the LID to a
                # phone number, so a phone-number allowlist can NEVER match this
                # sender — allowlist the LID shown in `sender` instead.
                sender_pn=normalize_number(sender_pn or "") or "unresolved",
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
                    await self._resolve_allow_lids()
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
            if health.get("outdated"):
                # WhatsApp rejected the bridge as too old (error 405). Auto-rebuild
                # it from source; if a rebuild happened the proc is gone and the
                # supervisor respawns the new binary, so stop waiting here.
                if await self._handle_outdated(proc):
                    return False
                await asyncio.sleep(_PAIR_POLL_INTERVAL)
                continue
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
        pt = self._is_pt()
        text = (
            "⚠ WhatsApp não está pareado (sem sessão ativa). Rode "
            "`python -m octoops --setup` na máquina e escaneie o QR code. "
            "O WhatsApp fica offline até lá."
            if pt
            else "⚠ WhatsApp is not paired (no active session). Run "
            "`python -m octoops --setup` on the machine and scan the QR code. "
            "WhatsApp stays offline until then."
        )
        ok = await self._send_telegram_admin(text)
        if ok:
            _log.info("whatsapp.unpaired_admin_notified")
        return ok

    def _is_pt(self) -> bool:
        reg = self._registry
        if reg is None:
            return False
        return reg.config.core.language.strip().lower().startswith("pt")

    async def _send_telegram_admin(self, text: str) -> bool:
        """Send an admin alert over Telegram (best-effort).

        Returns True when delivered, False when the Telegram transport isn't up
        yet or the send fails. Used for the unpaired notice and the auto-update
        alerts — Telegram is the reliable channel when WhatsApp itself is down.
        """
        reg = self._registry
        if reg is None:
            return False
        telegram = reg.transports.get("telegram")
        if telegram is None:
            return False
        try:
            await telegram.send(
                Response(text=text, chat_id=reg.config.telegram.admin_chat_id)
            )
            return True
        except Exception as exc:  # noqa: BLE001 - telegram may not be started yet
            _log.warning("whatsapp.admin_telegram_failed", error=str(exc))
            return False

    # --- auto-update on outdated (error 405) ----------------------------------

    async def _handle_outdated(self, proc: asyncio.subprocess.Process) -> bool:
        """React to WhatsApp rejecting the bridge as outdated (error 405).

        Rebuilds the bridge from source with the Go toolchain (no re-pair: the
        session lives in whatsmeow.db, only the binary changes). A cooldown stops
        a rebuild loop when the freshly built bridge is *still* rejected. Returns
        True if a rebuild was attempted (proc is then stopped and the supervisor
        respawns the new binary); False if skipped while cooling down.
        """
        now = time.monotonic()
        if (
            self._last_rebuild_at is not None
            and (now - self._last_rebuild_at) < _REBUILD_COOLDOWN
        ):
            if not self._outdated_cooldown_notified:
                self._outdated_cooldown_notified = True
                _log.error("whatsapp.bridge_still_outdated")
                await self._send_telegram_admin(self._outdated_manual_text())
            return False

        self._last_rebuild_at = now
        self._outdated_cooldown_notified = False
        _log.error("whatsapp.bridge_outdated", action="auto_rebuild")
        pt = self._is_pt()
        await self._send_telegram_admin(
            "⚠️ O WhatsApp rejeitou a ponte por estar desatualizada (erro 405). "
            "Atualizando e recompilando automaticamente — pode levar alguns "
            "minutos. O WhatsApp volta sozinho quando terminar (sem parear de novo)."
            if pt
            else "⚠️ WhatsApp rejected the bridge as outdated (error 405). "
            "Updating and rebuilding it automatically — this can take a few "
            "minutes. WhatsApp comes back on its own when it's done (no re-pairing)."
        )
        ok = await self._rebuild_bridge(proc)
        if ok:
            await self._send_telegram_admin(
                "✅ Ponte do WhatsApp atualizada. Reconectando…"
                if pt
                else "✅ WhatsApp bridge updated. Reconnecting…"
            )
        else:
            await self._send_telegram_admin(self._outdated_manual_text())
        return True

    def _outdated_manual_text(self) -> str:
        """Fallback message with manual rebuild steps when auto-update can't run."""
        if self._is_pt():
            return (
                "❌ Não consegui atualizar a ponte do WhatsApp automaticamente. "
                "Na máquina, abra a pasta whatsmeow-bridge e rode:\n"
                "  go get -u go.mau.fi/whatsmeow@latest\n"
                "  go mod tidy\n"
                "  go build -o ..\\whatsmeow-bridge.exe .\n"
                "Depois reinicie o OctoOps. (Precisa do Go instalado: https://go.dev/dl)"
            )
        return (
            "❌ Couldn't auto-update the WhatsApp bridge. On the machine, open the "
            "whatsmeow-bridge folder and run:\n"
            "  go get -u go.mau.fi/whatsmeow@latest\n"
            "  go mod tidy\n"
            "  go build -o ..\\whatsmeow-bridge.exe .\n"
            "Then restart OctoOps. (Needs Go installed: https://go.dev/dl)"
        )

    def _bridge_source_dir(self) -> Path | None:
        """Locate the bridge Go source (the folder holding main.go/go.mod)."""
        candidates: list[Path] = []
        if self._registry is not None and self._registry.paths is not None:
            candidates.append(self._registry.paths.home / _BRIDGE_SRC_DIRNAME)
        candidates.append(
            Path(self._bridge_path).resolve().parent / _BRIDGE_SRC_DIRNAME
        )
        for cand in candidates:
            if (cand / "main.go").exists():
                return cand
        return None

    def _bridge_output_path(self) -> Path:
        """Absolute path the rebuilt binary must be written to (the spawned exe)."""
        if self._registry is not None and self._registry.paths is not None:
            return self._registry.paths.resolve(self._bridge_path)
        return Path(self._bridge_path).resolve()

    async def _rebuild_bridge(self, proc: asyncio.subprocess.Process) -> bool:
        """Stop the bridge, rebuild it from source with Go, leave it stopped.

        The supervisor respawns it after this returns. Stopping first is required
        on Windows: the running .exe is locked, so `go build -o ...exe` would fail
        until the process exits. On any failure the old binary is left intact.
        """
        src = self._bridge_source_dir()
        if src is None:
            _log.error("whatsapp.rebuild_no_source")
            return False
        go = shutil.which("go")
        if go is None:
            _log.error("whatsapp.rebuild_no_go")
            return False
        out_path = self._bridge_output_path()
        # Free the .exe (Windows locks a running binary) before building.
        await self._stop_bridge_proc(proc)
        steps = (
            ("go_get", [go, "get", "-u", "go.mau.fi/whatsmeow@latest"]),
            ("go_mod_tidy", [go, "mod", "tidy"]),
            ("go_build", [go, "build", "-o", str(out_path), "."]),
        )
        for label, cmd in steps:
            rc, output = await self._run_build_step(cmd, src)
            if rc != 0:
                _log.error(
                    "whatsapp.rebuild_failed",
                    step=label,
                    code=rc,
                    output=output[-800:],
                )
                return False
        _log.info("whatsapp.rebuild_succeeded", output_path=str(out_path))
        return True

    async def _run_build_step(self, cmd: list[str], cwd: Path) -> tuple[int, str]:
        """Run one go command in cwd, capturing combined output. (rc, output)."""
        try:
            step = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:  # noqa: BLE001 - go missing/unspawnable
            return 1, str(exc)
        try:
            out, _ = await asyncio.wait_for(
                step.communicate(), timeout=_BUILD_TIMEOUT
            )
        except asyncio.TimeoutError:
            try:
                step.terminate()
            except ProcessLookupError:
                pass
            return 1, "build step timed out"
        return step.returncode or 0, out.decode("utf-8", "replace")

    async def _stop_bridge_proc(self, proc: asyncio.subprocess.Process) -> None:
        """Ask the bridge to shut down cleanly, then force it; wait for exit."""
        if proc.returncode is not None:
            return
        try:
            await asyncio.wait_for(self._client.shutdown(), timeout=_SHUTDOWN_TIMEOUT)
        except Exception:  # noqa: BLE001 - best effort; fall through to terminate
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=_SHUTDOWN_TIMEOUT)
            return
        except Exception:  # noqa: BLE001 - clean shutdown didn't land; force it
            pass
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_SHUTDOWN_TIMEOUT)
        except Exception:  # noqa: BLE001 - leave it to the supervisor/OS
            pass

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

    def _lid_cache_path(self) -> Path | None:
        reg = self._registry
        if reg is None or reg.paths is None:
            return None
        return reg.paths.data / "whatsapp_lids.json"

    def _load_lid_cache(self) -> dict[str, str]:
        """Last-known phone→LID mappings ({number: lid}); empty on missing/corrupt."""
        path = self._lid_cache_path()
        if path is None:
            return {}
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _save_lid_cache(self, mapping: dict[str, str]) -> None:
        path = self._lid_cache_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _log.info("whatsapp.lids_saved", path=str(path), count=len(mapping))
        except OSError as exc:  # noqa: BLE001 - caching is best-effort
            _log.warning("whatsapp.lids_save_failed", error=str(exc))

    async def _resolve_allow_lids(self) -> None:
        """Turn allowlisted phone numbers into the LIDs WhatsApp actually addresses.

        WhatsApp delivers many inbound messages under an opaque LID rather than the
        sender's phone number, so a phone-number allowlist never matches and modules
        stay unreachable. Right after pairing we ask the bridge to resolve each
        configured number to its LID (a live usync query that needs no prior contact)
        and add the LID to the in-memory allowlist — so an operator allowlists plain
        phone numbers and inbound just works, with no hand-copying a LID from logs.

        Best-effort and non-fatal: a cached mapping (data/whatsapp_lids.json) is
        seeded first so known LIDs keep working even if this refresh fails, and any
        error is logged without crashing the supervisor.
        """
        if not self._inbound_enabled or not self._configured_allow:
            return
        cache = self._load_lid_cache()
        # Seed from cache so a transient resolve failure doesn't lose known LIDs.
        for lid in cache.values():
            normalized = normalize_number(lid)
            if normalized:
                self._allow.add(normalized)
        for number in sorted(self._configured_allow):
            try:
                data = await self._client.resolve_lid(number)
            except Exception as exc:  # noqa: BLE001 - never crash the supervisor
                _log.warning("whatsapp.lid_resolve_failed", number=number, error=str(exc))
                continue
            if not data.get("ok"):
                _log.info("whatsapp.lid_unresolved", number=number)
                continue
            lid = normalize_number(data.get("lid") or "")
            if not lid:
                continue
            cache[number] = lid
            if lid not in self._allow:
                self._allow.add(lid)
                _log.info("whatsapp.lid_resolved", number=number, lid=lid)
        if cache:
            self._save_lid_cache(cache)

    async def _notify_admins(self) -> None:
        if not self._admin_chat_ids:
            return
        lang = self._registry.config.core.language if self._registry is not None else "en"
        text = _online_message(lang)
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
