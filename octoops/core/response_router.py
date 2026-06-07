"""Response routing — decides which transports deliver a response.

Transport-agnostic: it only knows transport *names* in registry.transports.
Current policy: Telegram (the control plane) always receives the response;
WhatsApp receives it only when response.mirror_to_whatsapp is set. The detailed
mirror routing rules are deferred — this is the single place they will live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from octoops.core.logging import get_logger

if TYPE_CHECKING:
    from octoops.core.registry import Registry
    from octoops.shared.models import Response

_log = get_logger("octoops.core.response_router")


async def route_response(response: "Response", registry: "Registry") -> None:
    telegram = registry.transports.get("telegram")
    if telegram is not None:
        try:
            await telegram.send(response)
        except Exception as exc:  # noqa: BLE001 - delivery failure must not crash dispatch
            _log.error("response.telegram_send_failed", error=str(exc))

    if response.mirror_to_whatsapp:
        whatsapp = registry.transports.get("whatsapp")
        if whatsapp is None:
            _log.warning("response.mirror_requested_no_whatsapp")
            return
        try:
            await whatsapp.send(response)
        except Exception as exc:  # noqa: BLE001
            _log.error("response.whatsapp_send_failed", error=str(exc))
