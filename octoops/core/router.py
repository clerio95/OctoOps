"""Command dispatch with authorization and error boundaries.

Commands live in a single global namespace; duplicate registration is fatal at
startup. dispatch() resolves the caller's role, authorizes, runs the handler in
a try/except, and logs received/completed/failed with latency.
"""

from __future__ import annotations

import time

from octoops.core.contracts import CommandDef
from octoops.core.errors import RouterError
from octoops.core.logging import get_logger
from octoops.core.permissions import Permissions
from octoops.core.registry import ModuleContext
from octoops.shared.models import Request, Response, Role

_log = get_logger("octoops.core.router")

_DENIED_MSG = "⛔ You are not authorized to run this command."
_UNKNOWN_MSG = "Unknown command. Try /status."
_ERROR_MSG = "⚠️ Something went wrong handling that command. It has been logged."


class Router:
    def __init__(self, permissions: Permissions) -> None:
        self._permissions = permissions
        self._commands: dict[str, tuple[CommandDef, ModuleContext]] = {}

    def register(self, command_def: CommandDef, ctx: ModuleContext) -> None:
        """Register a command. Duplicate name -> RouterError (fatal at startup)."""
        # Commands are matched case-insensitively; normalize on the way in.
        name = command_def.name.lstrip("/").lower()
        if name in self._commands:
            existing_ctx = self._commands[name][1]
            raise RouterError(
                f"duplicate command {name!r}: declared by both "
                f"{existing_ctx.name!r} and {ctx.name!r}"
            )
        self._commands[name] = (command_def, ctx)
        _log.info("command.registered", command=name, module=ctx.name)

    def unregister(self, name: str) -> None:
        """Remove a command (used to roll back a module whose registration failed)."""
        self._commands.pop(name.lstrip("/").lower(), None)

    def has_command(self, name: str) -> bool:
        return name.lstrip("/").lower() in self._commands

    def commands(self) -> dict[str, CommandDef]:
        return {name: cmd for name, (cmd, _ctx) in self._commands.items()}

    def command(self, name: str) -> CommandDef | None:
        entry = self._commands.get(name.lstrip("/").lower())
        return entry[0] if entry else None

    def entries(self) -> list[tuple[str, CommandDef, str]]:
        """(command_name, CommandDef, owning_module_name) for every command."""
        return [(name, cmd, ctx.name) for name, (cmd, ctx) in self._commands.items()]

    async def dispatch(
        self, request: Request, *, role_override: Role | None = None
    ) -> Response | None:
        """Dispatch a command.

        role_override is used only by the MCP server, which authenticates its
        client out-of-band and dispatches as a single configured service-role
        rather than a Telegram user. The normal (Telegram) path leaves it None
        and authorizes via Permissions.
        """
        name = request.command.lstrip("/").lower()
        entry = self._commands.get(name)
        if entry is None:
            # Unknown command -> silent help hint (no error).
            _log.info("command.unknown", command=name, user=request.user_id)
            return Response(text=_UNKNOWN_MSG, chat_id=request.chat_id)

        command_def, ctx = entry
        if role_override is not None:
            role = role_override
            authorized = role >= command_def.min_role
        else:
            role = self._permissions.role_for(request.user_id)
            authorized = self._permissions.authorize(request.user_id, command_def.min_role)
        _log.info(
            "command.received",
            command=name,
            module=ctx.name,
            user=request.user_id,
            source=request.source.value,
            role=role.name if role else "none",
        )

        if not authorized:
            _log.warning(
                "auth.denied",
                command=name,
                user=request.user_id,
                source=request.source.value,
                role=role.name if role else "none",
                required=command_def.min_role.name,
            )
            return Response(text=_DENIED_MSG, chat_id=request.chat_id)

        started = time.monotonic()
        try:
            response = await command_def.handler(request, ctx)
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            _log.info(
                "command.completed",
                command=name,
                module=ctx.name,
                latency_ms=latency_ms,
            )
            return response
        except Exception as exc:  # noqa: BLE001 - boundary: never propagate
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            _log.error(
                "command.failed",
                command=name,
                module=ctx.name,
                latency_ms=latency_ms,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return Response(text=_ERROR_MSG, chat_id=request.chat_id)
