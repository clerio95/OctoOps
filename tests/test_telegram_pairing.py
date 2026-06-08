"""Telegram onboarding helpers: token verification + /start chat-id capture.

All logic is exercised against a fake api, mirroring the WhatsApp pairing poll
tests — no live Telegram needed.
"""

import pytest

from octoops.wizard.telegram_pairing import (
    BotAlreadyRunningError,
    BotIdentity,
    StartResult,
    VerifyNetworkError,
    _match_start,
    make_start_link,
    new_nonce,
    verify_token,
    wait_for_start,
)


# --- token verification -------------------------------------------------------


class _MeApi:
    def __init__(self, payload):
        self._payload = payload

    async def get_me(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.mark.asyncio
async def test_verify_token_returns_identity_when_ok():
    api = _MeApi({"ok": True, "result": {"id": 42, "username": "MyBot", "first_name": "My"}})
    identity = await verify_token(api)
    assert identity == BotIdentity(id=42, username="MyBot", name="My")


@pytest.mark.asyncio
async def test_verify_token_none_when_not_ok():
    api = _MeApi({"ok": False, "error_code": 401, "description": "Unauthorized"})
    assert await verify_token(api) is None


@pytest.mark.asyncio
async def test_verify_token_raises_network_error_on_exception():
    api = _MeApi(RuntimeError("connection refused"))
    with pytest.raises(VerifyNetworkError, match="connection refused"):
        await verify_token(api)


# --- /start matching ----------------------------------------------------------


def test_match_start_accepts_exact_nonce_and_captures_user():
    update = {
        "message": {"text": "/start abc123", "chat": {"id": 777}, "from": {"id": 777}}
    }
    assert _match_start(update, "abc123") == StartResult(chat_id="777", user_id="777")


def test_match_start_distinguishes_chat_from_user_in_group():
    update = {
        "message": {"text": "/start abc", "chat": {"id": -1001}, "from": {"id": 555}}
    }
    assert _match_start(update, "abc") == StartResult(chat_id="-1001", user_id="555")


def test_match_start_rejects_wrong_or_missing_payload():
    assert _match_start({"message": {"text": "/start other", "chat": {"id": 1}}}, "abc") is None
    assert _match_start({"message": {"text": "hello", "chat": {"id": 1}}}, "abc") is None
    assert _match_start({}, "abc") is None


# --- getUpdates poll loop -----------------------------------------------------


class _UpdatesApi:
    """Serves scripted getUpdates batches and records offset acking."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.offsets = []

    async def get_updates(self, offset=None, timeout=0):
        self.offsets.append(offset)
        result = self._batches.pop(0) if self._batches else []
        return {"ok": True, "result": result}


@pytest.mark.asyncio
async def test_wait_for_start_returns_sender():
    batches = [
        [],  # nothing yet
        [{"update_id": 10, "message": {"text": "/start NONCE", "chat": {"id": 555}, "from": {"id": 555}}}],
    ]
    result = await wait_for_start(_UpdatesApi(batches), "NONCE", timeout=5, interval=0.01)
    assert result == StartResult(chat_id="555", user_id="555")


@pytest.mark.asyncio
async def test_wait_for_start_acks_consumed_updates():
    batches = [
        [{"update_id": 7, "message": {"text": "noise", "chat": {"id": 1}}}],
        [{"update_id": 8, "message": {"text": "/start NONCE", "chat": {"id": 9}, "from": {"id": 9}}}],
    ]
    api = _UpdatesApi(batches)
    result = await wait_for_start(api, "NONCE", timeout=5, interval=0.01)
    assert result.chat_id == "9"
    # First call has no offset; after consuming update 7 the next poll acks 8.
    assert api.offsets[0] is None
    assert 8 in api.offsets


@pytest.mark.asyncio
async def test_wait_for_start_ignores_other_nonce_until_timeout():
    api = _UpdatesApi([[{"update_id": 1, "message": {"text": "/start WRONG", "chat": {"id": 3}}}]])
    assert await wait_for_start(api, "RIGHT", timeout=0.05, interval=0.01) is None


@pytest.mark.asyncio
async def test_wait_for_start_survives_transient_errors():
    class _Flaky:
        def __init__(self):
            self._calls = 0

        async def get_updates(self, offset=None, timeout=0):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("blip")
            return {"result": [{"update_id": 2, "message": {"text": "/start N", "chat": {"id": 4}, "from": {"id": 4}}}]}

    result = await wait_for_start(_Flaky(), "N", timeout=5, interval=0.01)
    assert result.chat_id == "4"


@pytest.mark.asyncio
async def test_wait_for_start_raises_on_409_conflict():
    class _Busy:
        async def get_updates(self, offset=None, timeout=0):
            return {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates"}

    with pytest.raises(BotAlreadyRunningError):
        await wait_for_start(_Busy(), "N", timeout=5, interval=0.01)


# --- pure helpers -------------------------------------------------------------


def test_make_start_link():
    assert make_start_link("MyBot", "xyz") == "https://t.me/MyBot?start=xyz"


def test_new_nonce_is_unique_and_urlsafe():
    a, b = new_nonce(), new_nonce()
    assert a != b
    assert a.isascii() and "/" not in a and " " not in a
