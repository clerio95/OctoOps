import pytest

from octoops.core.response_router import route_response
from octoops.shared.models import Response
from octoops.transports import Transport


class FakeTransport(Transport):
    def __init__(self, name: str) -> None:
        self._name = name
        self.sent: list[Response] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, router, registry):  # pragma: no cover - unused
        pass

    async def send(self, response: Response) -> None:
        self.sent.append(response)


@pytest.mark.asyncio
async def test_telegram_always_receives(registry):
    tg = FakeTransport("telegram")
    wa = FakeTransport("whatsapp")
    registry.transports = {"telegram": tg, "whatsapp": wa}

    await route_response(Response(text="hi", chat_id="c1"), registry)
    assert len(tg.sent) == 1
    assert wa.sent == []  # not mirrored


@pytest.mark.asyncio
async def test_mirror_to_whatsapp(registry):
    tg = FakeTransport("telegram")
    wa = FakeTransport("whatsapp")
    registry.transports = {"telegram": tg, "whatsapp": wa}

    resp = Response(
        text="hi", chat_id="c1", mirror_to_whatsapp=True, whatsapp_chat_ids=["g1"]
    )
    await route_response(resp, registry)
    assert len(tg.sent) == 1
    assert len(wa.sent) == 1


@pytest.mark.asyncio
async def test_telegram_send_failure_does_not_raise(registry):
    class Boom(FakeTransport):
        async def send(self, response):
            raise RuntimeError("send failed")

    registry.transports = {"telegram": Boom("telegram")}
    # Must not propagate.
    await route_response(Response(text="hi", chat_id="c1"), registry)


@pytest.mark.asyncio
async def test_mirror_requested_but_no_whatsapp(registry):
    tg = FakeTransport("telegram")
    registry.transports = {"telegram": tg}  # no whatsapp
    resp = Response(text="hi", chat_id="c1", mirror_to_whatsapp=True)
    await route_response(resp, registry)  # logs a warning, does not raise
    assert len(tg.sent) == 1
