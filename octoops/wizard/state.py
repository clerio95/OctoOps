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
from octoops.wizard.i18n import DEFAULT_LANGUAGE, translate

_VALID_ROLES = ("viewer", "operator", "admin")

# Curated IANA zones offered as a dropdown in the wizard, so the common case is a
# pick-from-list instead of typing. America/Sao_Paulo leads as the default zone;
# anything outside this list can still be entered via the custom field.
COMMON_TIMEZONES = (
    "America/Sao_Paulo",
    "America/Bahia",
    "America/Fortaleza",
    "America/Manaus",
    "America/Rio_Branco",
    "America/Argentina/Buenos_Aires",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Mexico_City",
    "UTC",
    "Europe/Lisbon",
    "Europe/London",
    "Europe/Madrid",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Moscow",
    "Africa/Johannesburg",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Australia/Sydney",
)
DEFAULT_TIMEZONE = "America/Sao_Paulo"


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
    # JIDs/phone numbers that receive the startup status message on WhatsApp.
    whatsapp_admin_chat_ids: list[str] = field(default_factory=list)
    # [transport] optional brain-only inbound (whitelisted WhatsApp numbers -> /ask)
    whatsapp_inbound_enabled: bool = False
    whatsapp_allow: list[str] = field(default_factory=list)
    whatsapp_command: str = "ask"
    whatsapp_role: str = "operator"
    # [core]
    timezone: str = field(default_factory=lambda: detect_timezone(DEFAULT_TIMEZONE))
    allowed_user_ids: list[str] = field(default_factory=list)
    operator_user_ids: list[str] = field(default_factory=list)
    admin_user_ids: list[str] = field(default_factory=list)
    default_role: str = "viewer"
    log_file: str = "./logs/octoops.log"
    log_max_bytes: int = 10_000_000
    # Persisted UI/output language for modules (mirrors the wizard's own language
    # choice). Written to [core] language; modules localize their replies from it.
    language: str = DEFAULT_LANGUAGE
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


# Each validator takes an optional ``lang`` so its error string can be returned
# already translated; it defaults to English (the source language) so existing
# callers and unit tests that assert on the English text keep working unchanged.


def validate_required(value: str, lang: str = DEFAULT_LANGUAGE) -> str | None:
    return None if value.strip() else translate("validate.required", lang)


def validate_bot_token(value: str, lang: str = DEFAULT_LANGUAGE) -> str | None:
    if not value.strip():
        return translate("validate.required", lang)
    if ":" not in value:
        return translate("validate.bot_token_form", lang)
    return None


def validate_chat_id(value: str, lang: str = DEFAULT_LANGUAGE) -> str | None:
    """Telegram chat IDs are integers (can be negative for groups)."""
    if not value.strip():
        return translate("validate.required", lang)
    v = value.strip()
    if v.startswith("-"):
        v = v[1:]
    if not v.isdigit():
        return translate("validate.chat_id_numeric", lang)
    return None


def validate_user_id(value: str, lang: str = DEFAULT_LANGUAGE) -> str | None:
    v = value.strip()
    if not v.isdigit():
        return translate("validate.user_id_numeric", lang, value=value)
    return None


def validate_user_id_list(raw: str, lang: str = DEFAULT_LANGUAGE) -> str | None:
    for uid in parse_id_list(raw):
        err = validate_user_id(uid, lang)
        if err:
            return err
    return None


def validate_timezone(value: str, lang: str = DEFAULT_LANGUAGE) -> str | None:
    try:
        ZoneInfo(value.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return translate("validate.timezone_unknown", lang, value=value)
    return None


def validate_port(value: str, lang: str = DEFAULT_LANGUAGE) -> str | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return translate("validate.port_number", lang)
    if not (1 <= port <= 65535):
        return translate("validate.port_range", lang)
    return None


def validate_role(value: str, lang: str = DEFAULT_LANGUAGE) -> str | None:
    if value.strip().lower() not in _VALID_ROLES:
        return translate("validate.role_oneof", lang, roles=", ".join(_VALID_ROLES))
    return None


def validate_has_authorized_user(
    allowed: list[str],
    operators: list[str],
    admins: list[str],
    lang: str = DEFAULT_LANGUAGE,
) -> str | None:
    """At least one user must be authorized, or the bot is unusable (it silently
    ignores everyone). Any of the three lists satisfies this."""
    if allowed or operators or admins:
        return None
    return translate("validate.need_user", lang)


def validate_config_field(
    field_def: ConfigField, value: str, lang: str = DEFAULT_LANGUAGE
) -> str | None:
    """Validate one module config field value against its declared kind."""
    value = value.strip()
    if not value:
        return translate("validate.required", lang) if field_def.required else None

    kind = field_def.kind
    if kind is ConfigFieldKind.Integer:
        try:
            int(value)
        except ValueError:
            return translate("validate.field_integer", lang)
    elif kind is ConfigFieldKind.Boolean:
        if value.lower() not in ("true", "false", "yes", "no", "1", "0"):
            return translate("validate.field_boolean", lang)
    elif kind is ConfigFieldKind.IpAddress:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return translate("validate.field_ip", lang)
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
