"""McpService authorization + dispatch (pure, no MCP SDK)."""

import pytest

from octoops.core.config import McpConfig
from octoops.core.contracts import CommandDef
from octoops.core.router import Router
from octoops.mcp.service import McpService
from octoops.shared.models import Response, Role


async def _pong(request, ctx):
    return Response(text=f"pong:{' '.join(request.args)}", chat_id=request.chat_id)


async def _deployed(request, ctx):
    return Response(text="deployed", chat_id=request.chat_id)


def _router(permissions, module_ctx) -> Router:
    r = Router(permissions)
    r.register(CommandDef("ping", "Ping", Role.Viewer, _pong, ai_invokable=True), module_ctx)
    r.register(CommandDef("deploy", "Deploy", Role.Operator, _deployed, ai_invokable=True), module_ctx)
    r.register(CommandDef("secret", "Secret", Role.Viewer, _pong, ai_invokable=False), module_ctx)
    return r


def _service(registry, router, **cfg) -> McpService:
    base = dict(enabled=True, allow_command_execution=True, service_role=Role.Viewer)
    base.update(cfg)
    return McpService(registry, router, McpConfig(**base))


def test_module_catalog_exposes_flags(registry, permissions, module_ctx):
    svc = _service(registry, _router(permissions, module_ctx))
    cmds = {c["command"]: c for m in svc.module_catalog() for c in m["commands"]}
    assert cmds["ping"]["ai_invokable"] is True
    assert cmds["secret"]["ai_invokable"] is False
    assert cmds["deploy"]["min_role"] == "Operator"


def test_invokable_commands_filtered_by_role_and_optin(registry, permissions, module_ctx):
    viewer = _service(registry, _router(permissions, module_ctx), service_role=Role.Viewer)
    names = {n for n, _c in viewer.invokable_commands()}
    assert names == {"ping"}  # deploy needs Operator; secret not ai_invokable

    operator = _service(registry, _router(permissions, module_ctx), service_role=Role.Operator)
    assert {n for n, _c in operator.invokable_commands()} == {"ping", "deploy"}


def test_status_text(registry, permissions, module_ctx):
    svc = _service(registry, _router(permissions, module_ctx))
    text = svc.status_text()
    assert "Uptime:" in text and "service role: Viewer" in text


@pytest.mark.asyncio
async def test_invoke_happy_path(registry, permissions, module_ctx):
    svc = _service(registry, _router(permissions, module_ctx))
    assert await svc.invoke("ping", ["a", "b"]) == "pong:a b"


@pytest.mark.asyncio
async def test_invoke_blocked_when_execution_disabled(registry, permissions, module_ctx):
    svc = _service(registry, _router(permissions, module_ctx), allow_command_execution=False)
    assert "disabled" in (await svc.invoke("ping")).lower()


@pytest.mark.asyncio
async def test_invoke_non_optin_command_refused(registry, permissions, module_ctx):
    svc = _service(registry, _router(permissions, module_ctx))
    assert "not exposed" in (await svc.invoke("secret")).lower()


@pytest.mark.asyncio
async def test_invoke_insufficient_role(registry, permissions, module_ctx):
    svc = _service(registry, _router(permissions, module_ctx), service_role=Role.Viewer)
    assert "requires Operator" in await svc.invoke("deploy")


@pytest.mark.asyncio
async def test_invoke_unknown_command(registry, permissions, module_ctx):
    svc = _service(registry, _router(permissions, module_ctx))
    assert "unknown" in (await svc.invoke("nope")).lower()
