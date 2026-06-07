"""build_transports honors the WhatsApp enable flag."""

from __future__ import annotations

import dataclasses

from octoops.transports import build_transports


def test_builds_both_when_whatsapp_enabled(registry):
    # conftest's TransportConfig() defaults whatsapp_enabled=True.
    transports = build_transports(registry)
    assert set(transports) == {"telegram", "whatsapp"}


def test_skips_whatsapp_when_disabled(registry):
    registry.config.transport = dataclasses.replace(
        registry.config.transport, whatsapp_enabled=False
    )
    transports = build_transports(registry)
    assert set(transports) == {"telegram"}
