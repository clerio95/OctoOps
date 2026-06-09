"""help module: /help and /ajuda list role-filtered commands, localized framing."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from octoops.core.config import (
    AppConfig,
    CoreConfig,
    TelegramConfig,
    TransportConfig,
)
from octoops.core.contracts import CommandDef
from octoops.core.event_bus import EventBus
from octoops.core.permissions import Permissions
from octoops.core.registry import ModuleConfig, ModuleContext, Registry
from octoops.core.router import Router
from octoops.core.scheduler import Scheduler
from octoops.modules.help import handle_help, load
from octoops.shared.models import Request, Response, Role, TransportSource

TZ = "America/Sao_Paulo"


async def _noop(request, ctx) -> Response:  # placeholder handler for fixtures
    return Response(text="", chat_id=request.chat_id)


def _setup(language="en"):
    """Build a registry whose router holds a few commands at different roles."""
    cfg = AppConfig(
        telegram=TelegramConfig(bot_token="t", admin_chat_id="1"),
        transport=TransportConfig(),
        core=CoreConfig(
            timezone=TZ,
            allowed_user_ids=["100"],   # Viewer
            operator_user_ids=["200"],  # Operator
            admin_user_ids=["300"],     # Admin
            default_role=Role.Viewer,
            language=language,
        ),
    )
    perms = Permissions(
        allowed_user_ids=["100"], operator_user_ids=["200"], admin_user_ids=["300"]
    )
    registry = Registry(
        config=cfg,
        event_bus=EventBus(),
        scheduler=Scheduler(timezone=TZ),
        permissions=perms,
        start_time=datetime.now(ZoneInfo(TZ)),
    )
    router = Router(perms)
    registry.router = router

    def ctx_for(module: str) -> ModuleContext:
        return ModuleContext(module, ModuleConfig({}), registry, registry.event_bus, registry.scheduler)

    router.register(CommandDef("status", "Show status.", Role.Viewer, _noop), ctx_for("status"))
    router.register(CommandDef("ask", "Ask the assistant.", Role.Operator, _noop), ctx_for("brain"))
    router.register(CommandDef("grant", "Grant a role.", Role.Admin, _noop), ctx_for("access"))

    # Register the help module's own commands too.
    help_ctx = ctx_for("help")
    for cmd in load(help_ctx).commands:
        router.register(cmd, help_ctx)
    return registry, help_ctx


def _req(user_id: str) -> Request:
    return Request(
        command="help",
        args=[],
        raw_text="/help",
        user_id=user_id,
        chat_id="c1",
        source=TransportSource.Telegram,
    )


def test_registers_both_help_and_ajuda():
    registry, help_ctx = _setup("en")
    reg = load(help_ctx)
    names = {c.name for c in reg.commands}
    assert names == {"help", "ajuda"}
    assert all(c.min_role is Role.Viewer for c in reg.commands)
    assert reg.name == "Help"


def test_ptbr_display_is_ajuda():
    _, help_ctx = _setup("pt-BR")
    assert load(help_ctx).name == "Ajuda"


async def test_viewer_sees_only_viewer_commands():
    registry, help_ctx = _setup("en")
    text = (await handle_help(_req("100"), help_ctx)).text  # Viewer
    assert "/status" in text
    assert "/help" in text and "/ajuda" in text  # help is Viewer-level
    assert "/ask" not in text   # Operator
    assert "/grant" not in text  # Admin


async def test_operator_sees_operator_and_below():
    registry, help_ctx = _setup("en")
    text = (await handle_help(_req("200"), help_ctx)).text  # Operator
    assert "/status" in text and "/ask" in text
    assert "/grant" not in text


async def test_admin_sees_everything():
    registry, help_ctx = _setup("en")
    text = (await handle_help(_req("300"), help_ctx)).text  # Admin
    assert "/status" in text and "/ask" in text and "/grant" in text


async def test_descriptions_are_shown_as_authored():
    registry, help_ctx = _setup("en")
    text = (await handle_help(_req("300"), help_ctx)).text
    assert "Show status." in text
    assert "Grant a role." in text


async def test_grouped_by_module():
    registry, help_ctx = _setup("en")
    text = (await handle_help(_req("300"), help_ctx)).text
    assert "[status]" in text and "[brain]" in text and "[access]" in text


async def test_header_localizes_to_portuguese():
    registry, help_ctx = _setup("pt-BR")
    text = (await handle_help(_req("300"), help_ctx)).text
    assert "comandos que você pode usar" in text


async def test_unknown_user_role_none_shows_all():
    # A synthetic/privileged caller not in any role list -> show everything.
    registry, help_ctx = _setup("en")
    text = (await handle_help(_req("999"), help_ctx)).text
    assert "/grant" in text
