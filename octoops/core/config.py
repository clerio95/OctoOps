"""config.toml parsing and validation.

Reading uses stdlib tomllib (3.11+). Writing (wizard, Stage 3) uses tomlkit.
Modules never read this file directly — they receive a ModuleConfig view.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from octoops.core.errors import ConfigError
from octoops.shared.models import Role

DEFAULT_LOG_MAX_BYTES = 10_000_000


def _as_str_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"[{key}] must be a list, got {type(value).__name__}")
    return [str(v) for v in value]


@dataclass
class TelegramConfig:
    bot_token: str
    admin_chat_id: str


@dataclass
class TransportConfig:
    # WhatsApp is output-only and optional. When disabled, the bridge sidecar and
    # callback server are never started (Telegram-only deployments). Defaults True
    # so configs predating this flag keep their previous behavior.
    whatsapp_enabled: bool = True
    whatsapp_bridge_path: str = "./whatsmeow-bridge.exe"
    whatsapp_bridge_port: int = 3000
    octoops_callback_port: int = 3001
    # Phone numbers (JIDs or digits) that receive startup status notifications.
    whatsapp_admin_chat_ids: list[str] = field(default_factory=list)
    # Optional inbound: let whitelisted WhatsApp numbers reach ONE command (the
    # embedded brain by default). Off by default — WhatsApp stays output-only.
    # whatsapp_command is forced on every inbound message, so a WhatsApp user can
    # only ever invoke that one command (e.g. /ask), never the rest of the bot.
    whatsapp_inbound_enabled: bool = False
    whatsapp_allow: list[str] = field(default_factory=list)
    whatsapp_command: str = "ask"
    whatsapp_role: Role = Role.Operator


@dataclass
class CoreConfig:
    timezone: str
    allowed_user_ids: list[str] = field(default_factory=list)
    operator_user_ids: list[str] = field(default_factory=list)
    admin_user_ids: list[str] = field(default_factory=list)
    default_role: Role = Role.Viewer
    log_file: str = "./logs/octoops.log"
    log_max_bytes: int = DEFAULT_LOG_MAX_BYTES


@dataclass
class McpConfig:
    """Optional MCP server (Stage 4). Off by default; a second control plane."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 3002
    service_role: Role = Role.Viewer
    # Master gate for command execution; even ai_invokable commands need this on.
    allow_command_execution: bool = False
    # Bearer token clients must present (defense in depth on the loopback bind).
    token: str | None = None


@dataclass
class AppConfig:
    telegram: TelegramConfig
    transport: TransportConfig
    core: CoreConfig
    mcp: McpConfig = field(default_factory=McpConfig)
    enabled_modules: list[str] = field(default_factory=list)
    # Raw per-module config sections, keyed by module name.
    module_sections: dict[str, dict[str, Any]] = field(default_factory=dict)

    def module_config(self, name: str) -> dict[str, Any]:
        """Return the raw [modules.<name>] section (empty dict if absent)."""
        return self.module_sections.get(name, {})

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        path = Path(path)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        tg = data.get("telegram") or {}
        bot_token = tg.get("bot_token")
        if not bot_token:
            raise ConfigError("missing required key: [telegram] bot_token")
        admin_chat_id = tg.get("admin_chat_id")
        if not admin_chat_id:
            raise ConfigError("missing required key: [telegram] admin_chat_id")

        tr = data.get("transport") or {}
        wa_role_raw = tr.get("whatsapp_role", "operator")
        try:
            wa_role = Role.from_str(str(wa_role_raw))
        except ValueError as exc:
            raise ConfigError(f"[transport] whatsapp_role: {exc}") from exc
        transport = TransportConfig(
            whatsapp_enabled=bool(
                tr.get("whatsapp_enabled", TransportConfig.whatsapp_enabled)
            ),
            whatsapp_bridge_path=str(
                tr.get("whatsapp_bridge_path", TransportConfig.whatsapp_bridge_path)
            ),
            whatsapp_bridge_port=int(
                tr.get("whatsapp_bridge_port", TransportConfig.whatsapp_bridge_port)
            ),
            octoops_callback_port=int(
                tr.get("octoops_callback_port", TransportConfig.octoops_callback_port)
            ),
            whatsapp_admin_chat_ids=_as_str_list(
                tr.get("whatsapp_admin_chat_ids"), "transport.whatsapp_admin_chat_ids"
            ),
            whatsapp_inbound_enabled=bool(
                tr.get("whatsapp_inbound_enabled", False)
            ),
            whatsapp_allow=_as_str_list(
                tr.get("whatsapp_allow"), "transport.whatsapp_allow"
            ),
            whatsapp_command=str(tr.get("whatsapp_command", "ask")),
            whatsapp_role=wa_role,
        )

        co = data.get("core") or {}
        timezone = co.get("timezone")
        if not timezone:
            raise ConfigError("missing required key: [core] timezone")
        default_role_raw = co.get("default_role", "viewer")
        try:
            default_role = Role.from_str(str(default_role_raw))
        except ValueError as exc:
            raise ConfigError(f"[core] default_role: {exc}") from exc
        core = CoreConfig(
            timezone=str(timezone),
            allowed_user_ids=_as_str_list(
                co.get("allowed_user_ids"), "core.allowed_user_ids"
            ),
            operator_user_ids=_as_str_list(
                co.get("operator_user_ids"), "core.operator_user_ids"
            ),
            admin_user_ids=_as_str_list(
                co.get("admin_user_ids"), "core.admin_user_ids"
            ),
            default_role=default_role,
            log_file=str(co.get("log_file", CoreConfig.log_file)),
            log_max_bytes=int(co.get("log_max_bytes", DEFAULT_LOG_MAX_BYTES)),
        )

        mc = data.get("mcp") or {}
        mcp_role_raw = mc.get("service_role", "viewer")
        try:
            mcp_service_role = Role.from_str(str(mcp_role_raw))
        except ValueError as exc:
            raise ConfigError(f"[mcp] service_role: {exc}") from exc
        token = mc.get("token")
        mcp = McpConfig(
            enabled=bool(mc.get("enabled", False)),
            host=str(mc.get("host", McpConfig.host)),
            port=int(mc.get("port", McpConfig.port)),
            service_role=mcp_service_role,
            allow_command_execution=bool(mc.get("allow_command_execution", False)),
            token=str(token) if token else None,
        )

        modules = data.get("modules") or {}
        enabled = _as_str_list(modules.get("enabled"), "modules.enabled")
        # Everything under [modules] except 'enabled' is a per-module subtable.
        module_sections: dict[str, dict[str, Any]] = {
            key: value
            for key, value in modules.items()
            if key != "enabled" and isinstance(value, dict)
        }

        return cls(
            telegram=TelegramConfig(
                bot_token=str(bot_token), admin_chat_id=str(admin_chat_id)
            ),
            transport=transport,
            core=core,
            mcp=mcp,
            enabled_modules=enabled,
            module_sections=module_sections,
        )
