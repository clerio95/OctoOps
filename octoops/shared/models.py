"""Internal request/response and role types.

These are transport-agnostic. Transports translate to/from their wire formats;
core and modules only ever see these dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum

# Telegram user/chat IDs are carried as strings throughout the system.
UserId = str
ChatId = str


class Role(IntEnum):
    """Authorization level. Higher value = more privilege."""

    Viewer = 1
    Operator = 2
    Admin = 3

    @classmethod
    def from_str(cls, value: str) -> "Role":
        """Parse a case-insensitive role name (e.g. 'viewer'). Raises ValueError."""
        try:
            return cls[value.strip().capitalize()]
        except KeyError as exc:
            raise ValueError(f"unknown role: {value!r}") from exc


class TransportSource(Enum):
    Telegram = "telegram"
    WhatsApp = "whatsapp"
    Mcp = "mcp"


@dataclass
class Request:
    command: str
    args: list[str]
    raw_text: str
    user_id: UserId
    chat_id: ChatId
    source: TransportSource


@dataclass
class Response:
    text: str
    chat_id: ChatId
    reply_to: str | None = None
    # Extension point for the deferred Telegram -> WhatsApp relay feature.
    # Modules set these; the core response router decides delivery.
    mirror_to_whatsapp: bool = False
    whatsapp_chat_ids: list[str] = field(default_factory=list)
