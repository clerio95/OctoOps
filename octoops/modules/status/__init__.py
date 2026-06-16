"""status module — /status: uptime, loaded modules, and the caller's role.

Reference implementation of the module contract. No business logic, no imports
of other modules, no direct config/transport access beyond ctx.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from octoops.core.contracts import CommandDef, ModuleRegistration
from octoops.core.registry import ModuleContext
from octoops.shared.models import Request, Response, Role
from octoops.shared.text import humanize_timedelta


def load(ctx: ModuleContext) -> ModuleRegistration:
    return ModuleRegistration(
        name="status",
        commands=[
            CommandDef(
                name="status",
                description="Show uptime, loaded modules, and your role.",
                min_role=Role.Viewer,
                handler=handle_status,
            )
        ],
    )


def build_status_text(registry) -> str:
    """Return the status body (uptime + modules). Reusable by startup notifications."""
    tz = ZoneInfo(registry.config.core.timezone)
    uptime = datetime.now(tz) - registry.start_time
    modules = registry.module_names
    modules_line = ", ".join(sorted(modules)) if modules else "(none)"
    text = (
        "🐙 *OctoOps status*\n"
        f"Uptime: {humanize_timedelta(uptime)}\n"
        f"Modules ({len(modules)}): {modules_line}"
    )
    # A module that loaded but was disabled (e.g. a command-name collision) must
    # be visible to the operator, not just a log line.
    for error in getattr(registry, "module_errors", []) or []:
        text += f"\n⚠ disabled: {error}"
    return text


def _cap(name: str) -> str:
    """Capitalize the first letter only (leave the rest as authored)."""
    return name[:1].upper() + name[1:] if name else name


def build_startup_text(registry) -> str:
    """Slim 'online' notification sent when the bot comes up.

    Unlike build_status_text (the /status command, which keeps uptime and the
    caller's role), this drops the uptime line and capitalizes each module name —
    it's a clean "we're online + what's loaded" line for admins.
    """
    modules = registry.module_names
    modules_line = (
        ", ".join(_cap(m) for m in sorted(modules, key=str.lower)) if modules else "(none)"
    )
    text = "🐙 *OctoOps online*\n" f"Modules ({len(modules)}): {modules_line}"
    for error in getattr(registry, "module_errors", []) or []:
        text += f"\n⚠ disabled: {error}"
    return text


async def handle_status(request: Request, ctx: ModuleContext) -> Response:
    registry = ctx.registry
    role = registry.permissions.role_for(request.user_id)
    role_name = role.name if role is not None else "unknown"
    text = build_status_text(registry) + f"\nYour role: {role_name}"
    return Response(text=text, chat_id=request.chat_id)
