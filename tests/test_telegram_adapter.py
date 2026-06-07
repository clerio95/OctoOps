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
