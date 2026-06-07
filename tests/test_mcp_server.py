"""MCP server shell: tool/resource registration and the bearer middleware.

Skipped if the optional 'mcp' extra is not installed.
"""

import pytest

pytest.importorskip("mcp")

from octoops.core.config import McpConfig
from octoops.core.contracts import CommandDef
from octoops.core.router import Router
from octoops.mcp.server import BearerTokenMiddleware, build_mcp_server
from octoops.mcp.service import McpService
from octoops.shared.models import Response, Role


async def _h(request, ctx):
    return Response(text="ok", chat_id=request.chat_id)


def _service(registry, permissions, module_ctx, **cfg):
    r = Router(permissions)
    r.register(CommandDef("ping", "Ping", Role.Viewer, _h, ai_invokable=True), module_ctx)
    r.register(CommandDef("deploy", "Deploy", Role.Operator, _h, ai_invokable=True), module_ctx)
    base = dict(enabled=True, allow_command_execution=True, service_role=Role.Viewer)
    base.update(cfg)
    return McpService(registry, r, McpConfig(**base))


@pytest.mark.asyncio
async def test_server_registers_resources_and_optin_tools(registry, permissions, module_ctx):
    svc = _service(registry, permissions, module_ctx, service_role=Role.Viewer)
    mcp = build_mcp_server(svc)

    resources = {str(r.uri) for r in await mcp.list_resources()}
    assert {"octoops://modules", "octoops://status"} <= resources

    tools = {t.name for t in await mcp.list_tools()}
    assert "ping" in tools          # viewer-level, opt-in
    assert "deploy" not in tools    # operator-level > viewer service role


@pytest.mark.asyncio
async def test_no_tools_when_execution_disabled(registry, permissions, module_ctx):
    svc = _service(registry, permissions, module_ctx, allow_command_execution=False)
    mcp = build_mcp_server(svc)
    assert await mcp.list_tools() == []
    # resources are still available
    assert len(await mcp.list_resources()) == 2


@pytest.mark.asyncio
async def test_bearer_middleware_rejects_missing_token():
    sent = []

    async def app(scope, receive, send):
        sent.append("passed-through")

    mw = BearerTokenMiddleware(app, token="secret")
    scope = {"type": "http", "headers": []}

    async def receive():
        return {}

    async def send(msg):
        sent.append(msg)

    await mw(scope, receive, send)
    assert sent[0]["status"] == 401
    assert "passed-through" not in sent


@pytest.mark.asyncio
async def test_bearer_middleware_allows_correct_token():
    passed = []

    async def app(scope, receive, send):
        passed.append(True)

    mw = BearerTokenMiddleware(app, token="secret")
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer secret")]}
    await mw(scope, lambda: None, lambda m: None)
    assert passed == [True]
