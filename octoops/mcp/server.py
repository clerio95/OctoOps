"""Thin MCP-SDK shell over McpService.

Builds a FastMCP server exposing OctoOps' module catalog and status as resources,
and (only when command execution is enabled) one MCP tool per opt-in command.
Served over Streamable HTTP, bound to the configured loopback host, optionally
behind a bearer token. Requires the optional 'mcp' extra.
"""

from __future__ import annotations

import hmac
import json
from typing import TYPE_CHECKING

from octoops.core.logging import get_logger

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from octoops.mcp.service import McpService

_log = get_logger("octoops.mcp")


class BearerTokenMiddleware:
    """Minimal ASGI middleware: require `Authorization: Bearer <token>`."""

    def __init__(self, app, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            presented = headers.get(b"authorization") or b""
            if not hmac.compare_digest(presented, self._expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self._app(scope, receive, send)


def build_mcp_server(service: "McpService") -> "FastMCP":
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "OctoOps",
        host=service.host,
        port=service.port,
        stateless_http=True,
        instructions=(
            "Control surface for the OctoOps bot. Read octoops://modules and "
            "octoops://status; call command tools when available."
        ),
    )

    @mcp.resource("octoops://modules")
    def modules_resource() -> str:
        return json.dumps(service.module_catalog(), indent=2)

    @mcp.resource("octoops://status")
    def status_resource() -> str:
        return service.status_text()

    if service.allow_command_execution:
        for name, cmd in service.invokable_commands():
            _register_command_tool(mcp, service, name, cmd.description)

    return mcp


def _register_command_tool(mcp: "FastMCP", service: "McpService", name: str, description: str) -> None:
    def _make(command_name: str):
        async def _tool(args: list[str] | None = None) -> str:
            """Invoke an OctoOps command. `args` are the positional arguments."""
            return await service.invoke(command_name, args or [])

        return _tool

    mcp.add_tool(_make(name), name=name, description=description)


async def serve_mcp(service: "McpService") -> None:
    """Run the MCP server over Streamable HTTP until cancelled."""
    import uvicorn

    from octoops.core.config import is_loopback_host

    if not is_loopback_host(service.host):
        _log.warning(
            "mcp.non_loopback_bind",
            host=service.host,
            hint="MCP is reachable beyond localhost; ensure the token and network are trusted",
        )

    mcp = build_mcp_server(service)
    app = mcp.streamable_http_app()
    if service.token:
        app.add_middleware(BearerTokenMiddleware, token=service.token)

    config = uvicorn.Config(
        app,
        host=service.host,
        port=service.port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    _log.info(
        "mcp.serving",
        host=service.host,
        port=service.port,
        execution=service.allow_command_execution,
        token_protected=bool(service.token),
    )
    await server.serve()
