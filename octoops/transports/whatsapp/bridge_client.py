"""aiohttp client for the Whatsmeow Go bridge's local HTTP REST API.

Endpoints (bridge side): POST /send, GET /health, POST /register-callback,
POST /shutdown. The session is created lazily and reused.

Even though the bridge binds loopback, its API is unauthenticated by default,
which lets any local process send WhatsApp messages or redirect inbound traffic.
To close that, OctoOps mints a per-process shared secret and hands it to the
bridge via the BRIDGE_TOKEN env var; this client presents it as a bearer token on
every request (``set_auth_token``). When no token is set the header is omitted, so
an older bridge (or the interactive pairing flow) keeps working unchanged.
"""

from __future__ import annotations

import os
from typing import Any

import aiohttp

# Environment variables forwarded when spawning the bridge sidecar. The parent
# process environment can hold module secrets (BRAIN_API_KEY, anything loaded
# from .env) that the bridge has no business seeing, so the subprocess gets an
# allowlisted minimum — PATH/temp/locale, the Windows system vars the Go runtime
# needs, and TLS/proxy overrides — plus the BRIDGE_* values OctoOps sets itself.
_ENV_ALLOWLIST = {
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "TZ",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
}


def bridge_env(*, token: str = "", port: int | None = None) -> dict[str, str]:
    """Minimal environment for the bridge subprocess (never the full parent env).

    Allowlist matching is case-insensitive (Windows env keys come in arbitrary
    case; lowercase proxy vars are common on Linux), preserving the original key.
    """
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOWLIST}
    if token:
        env["BRIDGE_TOKEN"] = token
    if port is not None:
        env["BRIDGE_PORT"] = str(port)
    return env


class BridgeClient:
    def __init__(
        self, base_url: str, *, timeout: float = 10.0, token: str | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._token = token

    def set_auth_token(self, token: str | None) -> None:
        """Set the bearer token sent with every request (None disables it)."""
        self._token = token

    def _headers(self) -> dict[str, str] | None:
        return {"Authorization": f"Bearer {self._token}"} if self._token else None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        session = await self._session_get()
        async with session.post(
            f"{self._base}/send",
            json={"chat_id": chat_id, "text": text},
            headers=self._headers(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def health(self) -> dict[str, Any]:
        session = await self._session_get()
        async with session.get(f"{self._base}/health", headers=self._headers()) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def get_groups(self) -> list[dict[str, Any]]:
        """Return the bot's joined groups: [{"jid", "name", "participants"}, ...]."""
        session = await self._session_get()
        async with session.get(f"{self._base}/groups", headers=self._headers()) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            return data.get("groups", [])

    async def register_callback(self, url: str) -> dict[str, Any]:
        session = await self._session_get()
        async with session.post(
            f"{self._base}/register-callback",
            json={"url": url},
            headers=self._headers(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def shutdown(self) -> dict[str, Any] | None:
        session = await self._session_get()
        async with session.post(
            f"{self._base}/shutdown", headers=self._headers()
        ) as resp:
            return await resp.json(content_type=None)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
