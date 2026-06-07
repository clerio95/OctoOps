import asyncio

import pytest

from octoops.core.event_bus import EventBus


@pytest.mark.asyncio
async def test_fanout_to_all_listeners(module_ctx):
    bus = EventBus()
    seen: list[str] = []

    async def a(payload, ctx):
        seen.append(f"a:{payload}")

    async def b(payload, ctx):
        seen.append(f"b:{payload}")

    bus.subscribe("evt", a, module_ctx)
    bus.subscribe("evt", b, module_ctx)
    await bus.publish("evt", "x")
    await bus.drain()

    assert sorted(seen) == ["a:x", "b:x"]


@pytest.mark.asyncio
async def test_listener_error_isolated(module_ctx):
    bus = EventBus()
    survived: list[int] = []

    async def boom(payload, ctx):
        raise RuntimeError("listener exploded")

    async def ok(payload, ctx):
        survived.append(1)

    bus.subscribe("evt", boom, module_ctx)
    bus.subscribe("evt", ok, module_ctx)
    await bus.publish("evt", None)
    await bus.drain()

    # The good listener still ran despite the other raising.
    assert survived == [1]


@pytest.mark.asyncio
async def test_publish_no_listeners_is_noop(module_ctx):
    bus = EventBus()
    await bus.publish("nobody", {"k": "v"})  # must not raise
