"""McpService — the auth-bearing bridge between MCP and the Router.

Pure (no MCP-SDK dependency) so it can be unit-tested directly. Authorization
is fail-closed and layered:

  1. Global gate: command execution must be enabled ([mcp] allow_command_execution).
  2. Per-command opt-in: CommandDef.ai_invokable must be True.
  3. Role: the command's min_role must be <= the configured MCP service-role.

All MCP-invoked commands run as a single synthetic identity at the service-role,
dispatched through Router.dispatch(role_override=...) so the normal error-isolation,
latency logging, and audit trail apply. The MCP server never maps to a Telegram user.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from octoops.shared.models import Request, Role, TransportSource
from octoops.shared.text import humanize_timedelta

if TYPE_CHECKING:
    from octoops.core.config import McpConfig
    from octoops.core.registry import Registry
    from octoops.core.router import Router

# Synthetic identity for MCP-originated requests (never a real Telegram user).
MCP_USER_ID = "__mcp__"
MCP_CHAT_ID = "__mcp__"


class McpService:
    def __init__(self, registry: "Registry", router: "Router", config: "McpConfig") -> None:
        self._registry = registry
        self._router = router
        self._config = config

    # --- connection settings ---------------------------------------------------

    @property
    def host(self) -> str:
        return self._config.host

    @property
    def port(self) -> int:
        return self._config.port

    @property
    def token(self) -> str | None:
        return self._config.token

    @property
    def service_role(self) -> Role:
        return self._config.service_role

    @property
    def allow_command_execution(self) -> bool:
        return self._config.allow_command_execution

    # --- read-only resources ---------------------------------------------------

    def module_catalog(self) -> list[dict]:
        """Module → command metadata. Safe to expose (no secrets / message bodies)."""
        by_module: dict[str, dict] = {}
        for name, cmd, module in self._router.entries():
            entry = by_module.setdefault(module, {"module": module, "commands": []})
            entry["commands"].append(
                {
                    "command": name,
                    "description": cmd.description,
                    "min_role": cmd.min_role.name,
                    "ai_invokable": cmd.ai_invokable,
                }
            )
        return sorted(by_module.values(), key=lambda m: m["module"])

    def status_text(self) -> str:
        reg = self._registry
        tz = ZoneInfo(reg.config.core.timezone)
        uptime = datetime.now(tz) - reg.start_time
        modules = ", ".join(sorted(reg.module_names)) or "(none)"
        return (
            "OctoOps (via MCP)\n"
            f"Uptime: {humanize_timedelta(uptime)}\n"
            f"Modules ({len(reg.module_names)}): {modules}\n"
            f"MCP service role: {self.service_role.name}\n"
            f"Command execution: {'enabled' if self.allow_command_execution else 'disabled'}"
        )

    # --- command exposure / invocation -----------------------------------------

    def invokable_commands(self) -> list[tuple[str, "object"]]:
        """(name, CommandDef) for commands the MCP service may execute."""
        result = []
        for name, cmd, _module in self._router.entries():
            if cmd.ai_invokable and cmd.min_role <= self.service_role:
                result.append((name, cmd))
        return result

    async def invoke(self, command: str, args: list[str] | None = None) -> str:
        args = args or []
        if not self.allow_command_execution:
            return "⛔ Command execution is disabled for the MCP server."

        cmd = self._router.command(command)
        if cmd is None:
            return f"Unknown command: {command!r}."
        if not cmd.ai_invokable:
            return f"⛔ Command {command!r} is not exposed to the MCP server."
        if cmd.min_role > self.service_role:
            return (
                f"⛔ Command {command!r} requires {cmd.min_role.name}; "
                f"the MCP service role is {self.service_role.name}."
            )

        raw_text = "/" + command + ((" " + " ".join(args)) if args else "")
        request = Request(
            command=command,
            args=list(args),
            raw_text=raw_text,
            user_id=MCP_USER_ID,
            chat_id=MCP_CHAT_ID,
            source=TransportSource.Mcp,
        )
        response = await self._router.dispatch(request, role_override=self.service_role)
        return response.text if response is not None else ""
