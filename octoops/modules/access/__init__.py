"""access module — manage the whitelist from the bot's own Telegram interface.

Commands:
  /whoami                       (Viewer) — your user ID and resolved role.
  /users                        (Admin)  — everyone with a role + its source.
  /grant <role> <user_id>       (Admin)  — add a runtime grant (persisted).
  /revoke <user_id>             (Admin)  — remove a runtime grant (persisted).
  /invite <role>                (Admin)  — mint a one-time link to onboard a new
                                           user who isn't whitelisted yet.
  /invites                      (Admin)  — list pending invite links.

Runtime grants layer on top of config.toml and survive restarts (see
core.role_store). Config-declared users can't be changed here — edit config.toml.
Invites (core.invites) let a brand-new user self-onboard by tapping a nonce'd
/start link; the transport gate redeems it. The module touches only
ctx.registry.permissions and ctx.registry.invites; it holds no state itself.
"""

from __future__ import annotations

from octoops.core.contracts import CommandDef, ModuleRegistration
from octoops.core.errors import PermissionsError
from octoops.core.registry import ModuleContext
from octoops.shared.models import Request, Response, Role


def load(ctx: ModuleContext) -> ModuleRegistration:
    return ModuleRegistration(
        name="access",
        commands=[
            CommandDef(
                name="whoami",
                description="Show your user ID and current role.",
                min_role=Role.Viewer,
                handler=handle_whoami,
            ),
            CommandDef(
                name="users",
                description="List everyone with access and their role.",
                min_role=Role.Admin,
                handler=handle_users,
            ),
            CommandDef(
                name="grant",
                description="Grant a role to a user: /grant <viewer|operator|admin> <user_id>",
                min_role=Role.Admin,
                handler=handle_grant,
            ),
            CommandDef(
                name="revoke",
                description="Revoke a user's runtime access: /revoke <user_id>",
                min_role=Role.Admin,
                handler=handle_revoke,
            ),
            CommandDef(
                name="invite",
                description="Create a one-time onboarding link: /invite <viewer|operator|admin>",
                min_role=Role.Admin,
                handler=handle_invite,
            ),
            CommandDef(
                name="invites",
                description="List pending invite links.",
                min_role=Role.Admin,
                handler=handle_invites,
            ),
        ],
    )


def _invite_link(username: str | None, nonce: str) -> str:
    if username:
        return f"https://t.me/{username}?start={nonce}"
    # Username unknown (transport not started, e.g. tests) — give manual fallback.
    return f"have them send this to the bot: /start {nonce}"


async def handle_whoami(request: Request, ctx: ModuleContext) -> Response:
    role = ctx.registry.permissions.role_for(request.user_id)
    role_name = role.name if role is not None else "none"
    return Response(
        text=f"Your user ID is `{request.user_id}`.\nRole: {role_name}",
        chat_id=request.chat_id,
    )


async def handle_users(request: Request, ctx: ModuleContext) -> Response:
    perms = ctx.registry.permissions
    users = perms.known_users()
    if not users:
        return Response(text="No users have a role yet.", chat_id=request.chat_id)
    # Highest role first, then by id, for a stable readable list.
    ordered = sorted(users.items(), key=lambda kv: (-int(kv[1]), kv[0]))
    lines = ["👥 *Authorized users*"]
    for uid, role in ordered:
        source = "runtime" if perms.is_runtime_only(uid) else "config"
        lines.append(f"• `{uid}` — {role.name} ({source})")
    return Response(text="\n".join(lines), chat_id=request.chat_id)


async def handle_grant(request: Request, ctx: ModuleContext) -> Response:
    if len(request.args) != 2:
        return Response(
            text="Usage: /grant <viewer|operator|admin> <user_id>",
            chat_id=request.chat_id,
        )
    role_raw, uid = request.args
    try:
        role = Role.from_str(role_raw)
    except ValueError:
        return Response(
            text=f"Unknown role {role_raw!r}. Use viewer, operator, or admin.",
            chat_id=request.chat_id,
        )
    if not uid.isdigit():
        return Response(text=f"{uid!r} is not a numeric user ID.", chat_id=request.chat_id)
    ctx.registry.permissions.grant(uid, role)
    return Response(text=f"✓ Granted {role.name} to `{uid}`.", chat_id=request.chat_id)


async def handle_revoke(request: Request, ctx: ModuleContext) -> Response:
    if len(request.args) != 1:
        return Response(text="Usage: /revoke <user_id>", chat_id=request.chat_id)
    uid = request.args[0]
    try:
        ctx.registry.permissions.revoke(uid)
    except PermissionsError as exc:
        return Response(text=f"⚠ {exc}", chat_id=request.chat_id)
    return Response(text=f"✓ Revoked runtime access for `{uid}`.", chat_id=request.chat_id)


async def handle_invite(request: Request, ctx: ModuleContext) -> Response:
    if len(request.args) != 1:
        return Response(
            text="Usage: /invite <viewer|operator|admin>", chat_id=request.chat_id
        )
    try:
        role = Role.from_str(request.args[0])
    except ValueError:
        return Response(
            text=f"Unknown role {request.args[0]!r}. Use viewer, operator, or admin.",
            chat_id=request.chat_id,
        )
    invites = ctx.registry.invites
    if invites is None:
        return Response(text="Invites are not available.", chat_id=request.chat_id)
    invite = invites.create(role)
    link = _invite_link(ctx.registry.bot_username, invite.nonce)
    return Response(
        text=(
            f"✓ One-time {role.name} invite created (expires in 24h).\n"
            f"Send this to the new user — when they open it and press Start, "
            f"they're in:\n{link}"
        ),
        chat_id=request.chat_id,
    )


async def handle_invites(request: Request, ctx: ModuleContext) -> Response:
    invites = ctx.registry.invites
    pending = invites.pending() if invites is not None else []
    if not pending:
        return Response(text="No pending invites.", chat_id=request.chat_id)
    lines = ["📨 *Pending invites*"]
    for invite in pending:
        link = _invite_link(ctx.registry.bot_username, invite.nonce)
        lines.append(f"• {invite.role.name} — {link}")
    return Response(text="\n".join(lines), chat_id=request.chat_id)
