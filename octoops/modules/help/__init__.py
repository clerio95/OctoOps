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
from octoops.shared.models import Request, Response, Role

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
            CommandDef("help", desc, Role.Viewer, handle_help),
            CommandDef("ajuda", desc, Role.Viewer, handle_help),
        ],
    )


def _visible_commands(router, role: Role | None):
    """(module, command_name, description) for commands the role may run.

    A None role (synthetic/privileged caller, e.g. MCP) is shown everything.
    Duplicate descriptions for alias commands are kept — each real command lists.
    """
    items: list[tuple[str, str, str]] = []
    for name, cmd, module in router.entries():
        if role is None or cmd.min_role <= role:
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
    role = ctx.registry.permissions.role_for(request.user_id)
    text = _render(lang, _visible_commands(router, role))
    return Response(text=text, chat_id=request.chat_id)
