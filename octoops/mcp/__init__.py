"""Optional MCP server surface (Stage 4).

Exposes OctoOps as an MCP server so any MCP client (Claude Desktop, the Claude
API mcp_servers parameter, an agent) can read module/status resources and — when
explicitly enabled — invoke opt-in commands. Off by default; gated by [mcp] in
config. Requires the optional 'mcp' extra (pip install octoops[mcp]).

The auth-bearing logic lives in service.py (pure, testable); server.py is the
thin MCP-SDK shell.
"""
