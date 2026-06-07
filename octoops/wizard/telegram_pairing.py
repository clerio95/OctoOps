"""Telegram bot onboarding helpers for the setup wizard.

Two jobs that turn the hardest part of onboarding ("paste a token, then somehow
find your numeric chat ID") into "tap a link, press Start":

1. ``verify_token`` — confirm a pasted BotFather token actually works (getMe) and
   learn the bot's ``@username``, so the wizard can echo "✓ Connected to @Bot"
   instead of letting the user discover a typo three screens later.
2. ``wait_for_start`` — poll getUpdates for a ``/start <nonce>`` message and return
   the sender's chat id, so the operator never has to hunt for their numeric ID.
   The nonce both filters noise and proves the person who pressed Start is the one
   running the wizard.

All network access goes through a small injectable ``TelegramApi`` so the polling
logic is unit-testable against a fake, exactly like the WhatsApp pairing loop in
``pairing.py``. The whole flow is opt-in; manual chat-id entry remains a fallback
for offline installs.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

_API_ROOT = "https://api.telegram.org"
_POLL_INTERVAL = 2.0


class BotAlreadyRunningError(Exception):
    """Raised when getUpdates returns 409 — the token's bot is live elsewhere.

    Telegram allows only one getUpdates consumer per bot, so a production bot
    that's already polling makes auto-detection impossible. We surface this as a
    clear instruction rather than a silent three-minute timeout.
    """


class TelegramApi:
    """Minimal async client for the handful of Bot API calls the wizard needs.

    Injectable (``root`` override + a tiny surface) so tests can supply a fake.
    """

    def __init__(self, token: str, *, root: str = _API_ROOT, timeout: float = 15.0) -> None:
        self._base = f"{root}/bot{token}"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _call(self, method: str, **params: Any) -> dict[str, Any]:
        sess = await self._sess()
        async with sess.get(f"{self._base}/{method}", params=params) as resp:
            return await resp.json(content_type=None)

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")

    async def delete_webhook(self) -> dict[str, Any]:
        # Drop any registered webhook, otherwise getUpdates returns 409 Conflict.
        return await self._call("deleteWebhook")

    async def get_updates(self, offset: int | None = None, timeout: int = 0) -> dict[str, Any]:
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        return await self._call("getUpdates", **params)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


@dataclass
class BotIdentity:
    id: int | None
    username: str
    name: str


@dataclass
class StartResult:
    """Who pressed Start. ``chat_id`` targets notifications; ``user_id`` (the
    sender) is what the operator whitelist keys on. In a private chat they're the
    same value, but they're distinct concepts (and differ for group chats)."""

    chat_id: str
    user_id: str | None


def new_nonce() -> str:
    """A short URL-safe token used as the ``/start`` deep-link payload."""
    return secrets.token_urlsafe(9)


def make_start_link(username: str, nonce: str) -> str:
    """A deep link that opens the bot with a pre-filled ``/start <nonce>``."""
    return f"https://t.me/{username}?start={nonce}"


async def verify_token(api: TelegramApi) -> BotIdentity | None:
    """Return the bot's identity if the token is valid, else None.

    Any network/parse failure is treated as "couldn't verify" — the caller shows
    a try-again message rather than crashing the wizard.
    """
    try:
        data = await api.get_me()
    except Exception:  # noqa: BLE001 - any failure means "couldn't verify"
        return None
    if not data.get("ok"):
        return None
    result = data.get("result", {})
    username = result.get("username")
    if not username:
        return None
    return BotIdentity(
        id=result.get("id"),
        username=username,
        name=result.get("first_name", username),
    )


def _match_start(update: dict[str, Any], nonce: str) -> StartResult | None:
    """If this update is exactly ``/start <nonce>``, return who sent it."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None
    if (msg.get("text") or "").strip() != f"/start {nonce}":
        return None
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return None
    user_id = (msg.get("from") or {}).get("id")
    return StartResult(
        chat_id=str(chat_id),
        user_id=str(user_id) if user_id is not None else None,
    )


async def wait_for_start(
    api: TelegramApi,
    nonce: str,
    timeout: float,
    *,
    interval: float = _POLL_INTERVAL,
) -> StartResult | None:
    """Poll getUpdates until a ``/start <nonce>`` arrives; return who sent it.

    Returns None on timeout. Raises ``BotAlreadyRunningError`` if Telegram reports
    a 409 conflict (the token's bot is already polling elsewhere). Advances the
    update offset as it reads so consumed updates aren't re-delivered. Testable in
    isolation against a fake api.
    """
    deadline = time.monotonic() + timeout
    offset: int | None = None
    while time.monotonic() < deadline:
        try:
            data = await api.get_updates(offset=offset)
        except Exception:  # noqa: BLE001 - transient network; keep polling
            await asyncio.sleep(interval)
            continue
        if data.get("error_code") == 409:
            raise BotAlreadyRunningError(data.get("description", "Conflict"))
        for update in data.get("result", []):
            offset = update["update_id"] + 1  # ack so we don't reread it
            result = _match_start(update, nonce)
            if result is not None:
                return result
        await asyncio.sleep(interval)
    return None
