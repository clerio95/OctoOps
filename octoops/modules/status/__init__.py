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
    return (
        "🐙 *OctoOps status*\n"
        f"Uptime: {humanize_timedelta(uptime)}\n"
        f"Modules ({len(modules)}): {modules_line}"
    )


async def handle_status(request: Request, ctx: ModuleContext) -> Response:
    registry = ctx.registry
    role = registry.permissions.role_for(request.user_id)
    role_name = role.name if role is not None else "unknown"
    text = build_status_text(registry) + f"\nYour role: {role_name}"
    return Response(text=text, chat_id=request.chat_id)
