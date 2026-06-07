"""Transport ABC and the build_transports factory.

A transport is the only layer that knows about a concrete messaging backend.
Adding one means implementing this ABC and wiring it into build_transports —
no other core changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from octoops.core.logging import get_logger

if TYPE_CHECKING:
    from octoops.core.registry import Registry
    from octoops.core.router import Router
    from octoops.shared.models import Response

_log = get_logger("octoops.transports")


class Transport(ABC):
    @abstractmethod
    async def run(self, router: "Router", registry: "Registry") -> None:
        """Start the receiving loop. Blocks until shutdown."""

    @abstractmethod
    async def send(self, response: "Response") -> None:
        """Send a response via this transport."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique transport identifier, e.g. 'telegram', 'whatsapp'."""


def build_transports(registry: "Registry") -> dict[str, Transport]:
    """Instantiate the active transports based on config.

    Imports are local so core (and core tests) need not import telegram/aiohttp.
    """
    # Imported here to avoid pulling transport deps at module import time.
    from octoops.transports.telegram.adapter import TelegramTransport
    from octoops.transports.whatsapp.adapter import WhatsAppTransport

    cfg = registry.config
    transports: dict[str, Transport] = {
        "telegram": TelegramTransport(
            token=cfg.telegram.bot_token, admin_chat_id=cfg.telegram.admin_chat_id
        ),
    }
    # WhatsApp is optional output-only; skip it entirely when disabled so a
    # Telegram-only deployment never spawns a bridge or binds a callback port.
    if cfg.transport.whatsapp_enabled:
        bridge_path = cfg.transport.whatsapp_bridge_path
        if registry.paths is not None:
            bridge_path = str(registry.paths.resolve(bridge_path))
        transports["whatsapp"] = WhatsAppTransport(
            bridge_path=bridge_path,
            bridge_port=cfg.transport.whatsapp_bridge_port,
            callback_port=cfg.transport.octoops_callback_port,
            inbound_enabled=cfg.transport.whatsapp_inbound_enabled,
            allow=cfg.transport.whatsapp_allow,
            command=cfg.transport.whatsapp_command,
            role=cfg.transport.whatsapp_role,
        )
    else:
        _log.info("transports.whatsapp_disabled")

    _log.info("transports.built", count=len(transports), names=list(transports))
    return transports
