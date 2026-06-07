"""Module-facing contracts: the dataclasses a module's load() returns.

A module declares its commands, jobs, listeners, and config fields by returning
a ModuleRegistration. Core consumes these; modules never touch core internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from octoops.shared.models import Request, Response, Role

if TYPE_CHECKING:
    # Imported lazily to avoid a contracts <-> registry import cycle.
    from octoops.core.registry import ModuleContext

EventPayload = Any  # typically a dict describing a business event

CommandHandler = Callable[[Request, "ModuleContext"], Awaitable[Response]]
JobHandler = Callable[["ModuleContext"], Awaitable[None]]
ListenerHandler = Callable[[EventPayload, "ModuleContext"], Awaitable[None]]
LifecycleHook = Callable[["ModuleContext"], Awaitable[None]]


class ConfigFieldKind(Enum):
    Text = "text"
    Password = "password"
    FilePath = "filepath"
    IpAddress = "ipaddress"
    Integer = "integer"
    Boolean = "boolean"


@dataclass
class ConfigField:
    key: str
    label: str
    description: str
    required: bool
    default: Optional[str]
    kind: ConfigFieldKind


@dataclass
class CommandDef:
    name: str
    description: str
    min_role: Role
    handler: CommandHandler
    # If True, this command may be invoked by the optional MCP server (Stage 4),
    # still subject to the configured MCP service-role and global execution gate.
    # Defaults False — a command is never AI-invokable unless it opts in.
    ai_invokable: bool = False


@dataclass
class JobDef:
    name: str
    schedule: str  # cron expression, evaluated in the configured timezone
    handler: JobHandler


@dataclass
class ListenerDef:
    event: str
    handler: ListenerHandler


@dataclass
class ModuleRegistration:
    name: str
    commands: list[CommandDef] = field(default_factory=list)
    listeners: list[ListenerDef] = field(default_factory=list)
    jobs: list[JobDef] = field(default_factory=list)
    config_fields: list[ConfigField] = field(default_factory=list)
    on_startup: Optional[LifecycleHook] = None
    on_shutdown: Optional[LifecycleHook] = None
