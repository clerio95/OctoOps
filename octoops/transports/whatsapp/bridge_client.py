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

from typing import Any

import aiohttp


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
