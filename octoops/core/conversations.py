"""Per-user conversation state for multi-step command flows.

Most commands are stateless request -> response. A few (e.g. an interactive
"add a record" flow) need to ask follow-up questions. A module opens a
conversation for the calling (transport, user), stashes arbitrary state, and
the next message from that user is routed back to the same command so the module
can advance its own little state machine.

How a follow-up message reaches the module differs per transport:
- WhatsApp inbound already forces every message to one command, so a module is
  always re-entered there — it just consults its own conversation state.
- The Telegram transport treats a non-command message ("1", free text) as an
  unknown command. It consults this store first and, if a conversation is
  active, forwards the message to that command instead.

Conversations expire after a TTL so a half-finished flow never wedges a user.
The store is in-memory only (per process) — a restart simply drops pending
flows, which is the desired behavior.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from octoops.shared.models import TransportSource

DEFAULT_TTL_SECONDS = 600.0  # 10 minutes

# (transport source value, user id) — keep flows on different transports apart.
ConvKey = tuple[str, str]


def conversation_key(source: TransportSource, user_id: str) -> ConvKey:
    return (source.value, user_id)


@dataclass
class Conversation:
    """An open multi-step flow: which command owns it, plus free-form state."""

    command: str
    data: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


class ConversationStore:
    """In-memory map of (transport, user) -> open Conversation, with a TTL."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._convs: dict[ConvKey, Conversation] = {}

    def start(
        self, key: ConvKey, command: str, data: dict[str, Any] | None = None
    ) -> Conversation:
        """Open (or replace) a conversation for ``key`` and return it."""
        conv = Conversation(
            command=command, data=dict(data or {}), updated_at=self._clock()
        )
        self._convs[key] = conv
        return conv

    def get(self, key: ConvKey) -> Conversation | None:
        """Return the active conversation, or None if absent or expired.

        An expired conversation is dropped as a side effect so it can never come
        back to life on a later, faster call.
        """
        conv = self._convs.get(key)
        if conv is None:
            return None
        if self._clock() - conv.updated_at > self._ttl:
            del self._convs[key]
            return None
        return conv

    def touch(self, key: ConvKey) -> None:
        """Reset the TTL after activity on an existing conversation."""
        conv = self._convs.get(key)
        if conv is not None:
            conv.updated_at = self._clock()

    def end(self, key: ConvKey) -> None:
        self._convs.pop(key, None)

    def active(self, key: ConvKey) -> bool:
        return self.get(key) is not None
