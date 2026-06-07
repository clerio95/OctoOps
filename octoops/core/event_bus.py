"""Async pub/sub with per-listener error isolation.

Each listener runs in its own task. An exception in one listener is caught and
logged; it never affects other listeners or the publisher.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from octoops.core.contracts import EventPayload, ListenerHandler
from octoops.core.logging import get_logger

if TYPE_CHECKING:
    from octoops.core.registry import ModuleContext

_log = get_logger("octoops.core.event_bus")


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[tuple[ListenerHandler, "ModuleContext"]]] = {}
        self._tasks: set[asyncio.Task] = set()

    def subscribe(
        self, event: str, handler: ListenerHandler, ctx: "ModuleContext"
    ) -> None:
        """Register a listener for an event. Called during module load."""
        self._listeners.setdefault(event, []).append((handler, ctx))

    async def publish(self, event: str, payload: EventPayload) -> None:
        """Fan-out to all listeners for this event, each in its own task."""
        listeners = self._listeners.get(event, [])
        if not listeners:
            return
        for handler, ctx in listeners:
            task = asyncio.create_task(
                self._run_listener(event, handler, payload, ctx)
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_listener(
        self,
        event: str,
        handler: ListenerHandler,
        payload: EventPayload,
        ctx: "ModuleContext",
    ) -> None:
        try:
            await handler(payload, ctx)
        except Exception as exc:  # noqa: BLE001 - boundary: never propagate
            _log.error(
                "listener.failed",
                event=event,
                module=getattr(ctx, "name", "?"),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def drain(self) -> None:
        """Await any in-flight listener tasks (used during shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
