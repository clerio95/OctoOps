"""Registry, ModuleContext, and ModuleConfig — the dependency container.

The Registry holds all shared services. A ModuleContext is a per-module view of
it plus that module's config. Modules never construct these; core does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from octoops.core.errors import ConfigError

if TYPE_CHECKING:
    from octoops.core.config import AppConfig
    from octoops.core.event_bus import EventBus
    from octoops.core.invites import InviteStore
    from octoops.core.paths import AppPaths
    from octoops.core.permissions import Permissions
    from octoops.core.scheduler import Scheduler
    from octoops.transports import Transport


class ModuleConfig:
    """Read-only view of a module's [modules.<name>] section."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = dict(data or {})

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        value = self._data.get(key, default)
        return value if value is not None else default

    def require(self, key: str) -> Any:
        if key not in self._data or self._data[key] in (None, ""):
            raise ConfigError(f"missing required module config key: {key!r}")
        return self._data[key]

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


@dataclass
class Registry:
    config: "AppConfig"
    event_bus: "EventBus"
    scheduler: "Scheduler"
    permissions: "Permissions"
    start_time: datetime
    paths: "AppPaths | None" = None
    transports: dict[str, "Transport"] = field(default_factory=dict)
    # Names of successfully loaded modules (for /status and introspection).
    module_names: list[str] = field(default_factory=list)
    # One-time invites for onboarding new users; the transport gate redeems them.
    invites: "InviteStore | None" = None
    # The bot's own @username, learned at startup — used to build invite links.
    bot_username: str | None = None


@dataclass
class ModuleContext:
    name: str
    config: ModuleConfig
    registry: Registry
    event_bus: "EventBus"
    scheduler: "Scheduler"
