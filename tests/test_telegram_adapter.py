from types import SimpleNamespace

import pytest
from telegram.constants import ParseMode

from octoops.transports.telegram.adapter import TelegramTransport, parse_command
from octoops.transports.telegram.formatter import format_response
from octoops.shared.models import Response


def test_parse_plain_command():
    assert parse_command("/status") == ("status", [])


def test_parse_command_with_args():
    assert parse_command("/deploy app1 prod") == ("deploy", ["app1", "prod"])


def test_parse_strips_botname_suffix():
    assert parse_command("/status@OctoOpsBot") == ("status", [])


def test_parse_lowercases_command_only():
    cmd, args = parse_command("/Echo Hello World")
    assert cmd == "echo"
    assert args == ["Hello", "World"]  # args preserve case


def test_parse_without_leading_slash():
    assert parse_command("status now") == ("status", ["now"])


def test_parse_empty():
    assert parse_command("   ") == ("", [])


def test_format_response_requests_markdown():
    kwargs = format_response(Response(text="*hi*", chat_id="1"))
    assert kwargs["text"] == "*hi*"
    assert kwargs["parse_mode"] == ParseMode.MARKDOWN


class _RecordingRouter:
    def __init__(self):
        self.dispatched = []

    def has_command(self, name):
        return True

    async def dispatch(self, request):
        self.dispatched.append(request)
        return None  # no response -> no downstream send


def _fake_update(user_id: str, text: str = "/status"):
    return SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id="chat1"),
    )


@pytest.mark.asyncio
async def test_unknown_user_is_silently_ignored(registry):
    transport = TelegramTransport(token="x")
    router = _RecordingRouter()
    transport._router = router
    transport._registry = registry  # allows 100/200/300 only

    await transport._on_message(_fake_update("999"), None)  # not in any list
    assert router.dispatched == []  # dropped before dispatch


@pytest.mark.asyncio
async def test_known_user_reaches_dispatch(registry):
    transport = TelegramTransport(token="x")
    router = _RecordingRouter()
    transport._router = router
    transport._registry = registry

    await transport._on_message(_fake_update("300"), None)  # admin
    assert len(router.dispatched) == 1
    assert router.dispatched[0].command == "status"


@pytest.mark.asyncio
async def test_active_conversation_forwards_plain_reply(registry):
    """A non-command message during an open flow is routed to the owning command."""
    from octoops.core.conversations import conversation_key
    from octoops.shared.models import TransportSource

    transport = TelegramTransport(token="x")
    router = _RecordingRouter()
    transport._router = router
    transport._registry = registry

    key = conversation_key(TransportSource.Telegram, "300")
    registry.conversations.start(key, command="deadlines", data={"step": "menu"})

    await transport._on_message(_fake_update("300", text="1"), None)
    assert len(router.dispatched) == 1
    req = router.dispatched[0]
    assert req.command == "deadlines"   # forwarded to the active command, not "1"
    assert req.args == ["1"]            # the whole reply becomes the argument


@pytest.mark.asyncio
async def test_slash_command_escapes_active_conversation(registry):
    """A '/'-message during a flow starts fresh (escape hatch), not a continuation."""
    from octoops.core.conversations import conversation_key
    from octoops.shared.models import TransportSource

    transport = TelegramTransport(token="x")
    router = _RecordingRouter()
    transport._router = router
    transport._registry = registry

    key = conversation_key(TransportSource.Telegram, "300")
    registry.conversations.start(key, command="deadlines", data={"step": "menu"})

    await transport._on_message(_fake_update("300", text="/status"), None)
    assert router.dispatched[0].command == "status"  # not forwarded to deadlines


@pytest.mark.asyncio
async def test_expired_conversation_forwards_stale_reply_for_timeout_notice(registry):
    """A plain reply just after a flow timed out is still forwarded to the owning
    command (once), so the module can tell the user it expired instead of silence."""
    from octoops.core.conversations import ConversationStore, conversation_key
    from octoops.shared.models import TransportSource

    transport = TelegramTransport(token="x")
    router = _RecordingRouter()
    transport._router = router
    transport._registry = registry

    now = [1000.0]
    registry.conversations = ConversationStore(ttl_seconds=10.0, clock=lambda: now[0])
    key = conversation_key(TransportSource.Telegram, "300")
    registry.conversations.start(key, command="deadlines", data={"step": "menu"})
    now[0] += 11.0  # the flow times out

    await transport._on_message(_fake_update("300", text="3"), None)
    assert len(router.dispatched) == 1
    req = router.dispatched[0]
    assert req.command == "deadlines"  # forwarded to the expired flow's command
    assert req.args == ["3"]
