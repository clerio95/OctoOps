"""help module — /help (and /ajuda): list the commands the caller can run.

Both command names are always registered, so a user can discover the bot in
English or Portuguese regardless of the install language; the surrounding text
(title, empty-list line) is localized via ``core.language``. Individual command
descriptions are shown exactly as each owning module authored them.

The list is role-filtered: a Viewer sees only Viewer-level commands, an Admin
sees everything. Reads the registered commands from ``ctx.registry.router`` —
introspection only; it imports no other module and never raises out of the
handler.
"""

from __future__ import annotations

from octoops.core.contracts import CommandDef, ModuleRegistration
from octoops.core.registry import ModuleContext
from octoops.shared.models import Request, Response, Role, TransportSource

from .i18n import tr

_REPLY_LIMIT = 3900  # stay under Telegram's ~4096 cap


def _lang(ctx: ModuleContext) -> str:
    return ctx.registry.config.core.language


def load(ctx: ModuleContext) -> ModuleRegistration:
    lang = _lang(ctx)
    desc = tr(lang, "desc")
    # Register both names so /help and /ajuda always work; same handler.
    return ModuleRegistration(
        name=tr(lang, "display"),
        commands=[
            CommandDef("help", desc, Role.Viewer, handle_help, whatsapp_keywords=["help"]),
            CommandDef(
                "ajuda", desc, Role.Viewer, handle_help, whatsapp_keywords=["ajuda"]
            ),
        ],
    )


def _visible_commands(router, role: Role | None, *, whatsapp_only=False, default_command=""):
    """(module, command_name, description) for commands the role may run.

    A None role (synthetic/privileged caller, e.g. MCP) is shown everything.
    Duplicate descriptions for alias commands are kept — each real command lists.

    With ``whatsapp_only`` set, the list is further narrowed to what a WhatsApp
    user can actually reach: commands that declare a whatsapp_keyword, plus the
    one forced default command (``default_command``).
    """
    items: list[tuple[str, str, str]] = []
    for name, cmd, module in router.entries():
        if role is not None and not (cmd.min_role <= role):
            continue
        if whatsapp_only and not (
            getattr(cmd, "whatsapp_keywords", None) or name == default_command
        ):
            continue
        items.append((module, name, cmd.description))
    items.sort(key=lambda t: (t[0].lower(), t[1].lower()))
    return items


def _render(lang: str, items: list[tuple[str, str, str]]) -> str:
    if not items:
        return tr(lang, "none")
    body = tr(lang, "header")
    current_module = None
    for module, name, description in items:
        if module != current_module:
            body += f"\n\n[{module}]"
            current_module = module
        line = f"\n/{name} — {description}"
        if len(body) + len(line) > _REPLY_LIMIT:
            body += "\n…"
            break
        body += line
    return body


async def handle_help(request: Request, ctx: ModuleContext) -> Response:
    lang = _lang(ctx)
    router = ctx.registry.router
    if router is None:  # defensive: router is wired in bootstrap
        return Response(text=tr(lang, "none"), chat_id=request.chat_id)
    if request.source == TransportSource.WhatsApp:
        # WhatsApp inbound is limited to keyword commands and the one forced
        # default command, evaluated at the configured WhatsApp role — so show
        # only those, not the full Telegram command set.
        transport = ctx.registry.config.transport
        role = transport.whatsapp_role
        default_command = (transport.whatsapp_command or "").lstrip("/").lower()
        items = _visible_commands(
            router, role, whatsapp_only=True, default_command=default_command
        )
    else:
        role = ctx.registry.permissions.role_for(request.user_id)
        items = _visible_commands(router, role)
    text = _render(lang, items)
    return Response(text=text, chat_id=request.chat_id)
