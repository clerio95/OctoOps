"""WizardState and pure validation helpers.

All the wizard's real decisions live here, free of any Textual dependency, so
they can be unit-tested directly. The Textual screens only collect user input
into a WizardState and call these validators.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from octoops.core.contracts import ConfigField, ConfigFieldKind

_VALID_ROLES = ("viewer", "operator", "admin")


def detect_timezone(default: str = "UTC") -> str:
    """Best-effort local IANA timezone, for pre-filling the wizard's tz field.

    Checks the TZ env var, the /etc/localtime symlink, then /etc/timezone
    (Linux/macOS). Windows has no native IANA name, so it falls back to ``default``
    (UTC) — a neutral default beats hardcoding one region. Every candidate is
    validated against the tz database before being returned.
    """
    candidates: list[str] = []
    if tz_env := os.environ.get("TZ"):
        candidates.append(tz_env)
    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        target = os.readlink(localtime)
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    etc_tz = Path("/etc/timezone")
    if etc_tz.is_file():
        try:
            candidates.append(etc_tz.read_text("utf-8").strip())
        except OSError:
            pass
    for cand in candidates:
        try:
            ZoneInfo(cand)
            return cand
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return default


@dataclass
class WizardState:
    # [telegram]
    bot_token: str = ""
    admin_chat_id: str = ""
    # [transport]
    use_whatsapp: bool = False
    whatsapp_bridge_path: str = "./whatsmeow-bridge.exe"
    whatsapp_bridge_port: int = 3000
    octoops_callback_port: int = 3001
    # [transport] optional brain-only inbound (whitelisted WhatsApp numbers -> /ask)
    whatsapp_inbound_enabled: bool = False
    whatsapp_allow: list[str] = field(default_factory=list)
    whatsapp_command: str = "ask"
    whatsapp_role: str = "operator"
    # [core]
    timezone: str = field(default_factory=detect_timezone)
    allowed_user_ids: list[str] = field(default_factory=list)
    operator_user_ids: list[str] = field(default_factory=list)
    admin_user_ids: list[str] = field(default_factory=list)
    default_role: str = "viewer"
    log_file: str = "./logs/octoops.log"
    log_max_bytes: int = 10_000_000
    # [modules]
    enabled_modules: list[str] = field(default_factory=list)
    # [modules.<name>] -> {key: value}. Values are typed (str/int/bool) once
    # coerced by the module-config screen, or carried verbatim from an existing
    # config on a re-run.
    module_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Module Password fields, ENV_NAME -> value. Written to a private .env
    # sidecar (0600), NEVER config.toml; loaded into the environment at startup.
    secrets: dict[str, str] = field(default_factory=dict)
    # [mcp] carried verbatim from an existing config so re-running setup doesn't
    # reset a server the operator enabled by hand. None on fresh installs (the
    # writer emits the disabled default block instead). The wizard never edits MCP.
    mcp_section: dict[str, Any] | None = None
    # wizard-only (not written to config)
    register_task: bool = False


# --- field parsing / validation (return error string, or None if valid) -----


def parse_id_list(raw: str) -> list[str]:
    """Split a comma/space/newline separated list of IDs, dropping blanks."""
    parts = raw.replace(",", " ").split()
    return [p.strip() for p in parts if p.strip()]


def secret_env_name(module: str, key: str) -> str:
    """Env var name for a module Password field, e.g. ('brain','api_key') ->
    'BRAIN_API_KEY'. Non-alphanumerics become underscores."""
    return "".join(ch if ch.isalnum() else "_" for ch in f"{module}_{key}".upper())


def validate_required(value: str) -> str | None:
    return None if value.strip() else "required"


def validate_bot_token(value: str) -> str | None:
    if not value.strip():
        return "required"
    if ":" not in value:
        return "expected the form 123456:ABC-..."
    return None


def validate_chat_id(value: str) -> str | None:
    """Telegram chat IDs are integers (can be negative for groups)."""
    if not value.strip():
        return "required"
    v = value.strip()
    if v.startswith("-"):
        v = v[1:]
    if not v.isdigit():
        return "must be a numeric Telegram ID"
    return None


def validate_user_id(value: str) -> str | None:
    v = value.strip()
    if not v.isdigit():
        return f"{value!r} is not a numeric user ID"
    return None


def validate_user_id_list(raw: str) -> str | None:
    for uid in parse_id_list(raw):
        err = validate_user_id(uid)
        if err:
            return err
    return None


def validate_timezone(value: str) -> str | None:
    try:
        ZoneInfo(value.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return f"unknown IANA timezone: {value!r}"
    return None


def validate_port(value: str) -> str | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return "must be a number"
    if not (1 <= port <= 65535):
        return "must be between 1 and 65535"
    return None


def validate_role(value: str) -> str | None:
    if value.strip().lower() not in _VALID_ROLES:
        return f"must be one of {', '.join(_VALID_ROLES)}"
    return None


def validate_has_authorized_user(
    allowed: list[str], operators: list[str], admins: list[str]
) -> str | None:
    """At least one user must be authorized, or the bot is unusable (it silently
    ignores everyone). Any of the three lists satisfies this."""
    if allowed or operators or admins:
        return None
    return (
        "Authorize at least one user — add your Telegram user ID as an Admin "
        "(the Telegram step can capture it for you)."
    )


def validate_config_field(field_def: ConfigField, value: str) -> str | None:
    """Validate one module config field value against its declared kind."""
    value = value.strip()
    if not value:
        return "required" if field_def.required else None

    kind = field_def.kind
    if kind is ConfigFieldKind.Integer:
        try:
            int(value)
        except ValueError:
            return "must be an integer"
    elif kind is ConfigFieldKind.Boolean:
        if value.lower() not in ("true", "false", "yes", "no", "1", "0"):
            return "must be true/false"
    elif kind is ConfigFieldKind.IpAddress:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return "must be a valid IP address"
    return None


def coerce_config_value(field_def: ConfigField, value: str):
    """Coerce a validated string to the typed value written into config.toml."""
    value = value.strip()
    if not value:
        return None
    if field_def.kind is ConfigFieldKind.Integer:
        return int(value)
    if field_def.kind is ConfigFieldKind.Boolean:
        return value.lower() in ("true", "yes", "1")
    return value
