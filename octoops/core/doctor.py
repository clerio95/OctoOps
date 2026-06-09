"""`octoops --check` — setup diagnostics.

Validates the things that commonly block a first start: Python version, importable
dependencies, config validity, timezone, bridge binary presence, port availability,
and a writable log directory. Prints a checklist and returns a non-zero exit code
if any hard check fails. Never starts the bot.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import sys
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from octoops.core.config import AppConfig
from octoops.core.errors import OctoOpsError
from octoops.core.paths import AppPaths

_OK = "✓"
_FAIL = "✗"
_WARN = "!"

_REQUIRED_MODULES = ["telegram", "aiohttp", "apscheduler", "structlog", "tomlkit", "textual"]
# Matches pyproject requires-python and .python-version: the runtime is built and
# tested on 3.13, so don't pass a check on an unvalidated older interpreter.
_MIN_PYTHON = (3, 13)


class _Report:
    def __init__(self) -> None:
        self.failed = False
        self.warned = False

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  {_OK} {label}" + (f" — {detail}" if detail else ""))

    def warn(self, label: str, detail: str = "") -> None:
        self.warned = True
        print(f"  {_WARN} {label}" + (f" — {detail}" if detail else ""))

    def fail(self, label: str, detail: str = "") -> None:
        self.failed = True
        print(f"  {_FAIL} {label}" + (f" — {detail}" if detail else ""))


def _check_python(r: _Report) -> None:
    v = sys.version_info
    label = f"Python {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= _MIN_PYTHON:
        r.ok(label)
    else:
        r.fail(label, f"need >= {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}")


def _check_deps(r: _Report) -> None:
    for name in _REQUIRED_MODULES:
        if importlib.util.find_spec(name) is not None:
            r.ok(f"dependency: {name}")
        else:
            r.fail(f"dependency: {name}", "not installed (run: uv sync)")


def _port_free(port: int) -> bool:
    if port <= 0:
        return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _bridge_filename() -> str:
    return "whatsmeow-bridge.exe" if sys.platform.startswith("win") else "whatsmeow-bridge"


def _check_token_live(
    r: _Report, token: str, api_factory: Callable[[str], Any] | None
) -> None:
    """Confirm Telegram actually accepts the token (getMe). A rejected token is a
    hard FAIL (the bot can't start); a network error is only a WARN (can't tell).
    Lazy-imports the API client so the offline checks never pull aiohttp."""
    if not token:
        r.fail("telegram token (live)", "no bot_token configured")
        return
    if api_factory is None:
        from octoops.wizard.telegram_pairing import TelegramApi

        api_factory = TelegramApi

    async def _run() -> dict[str, Any]:
        api = api_factory(token)
        try:
            return await api.get_me()
        finally:
            await api.close()

    try:
        data = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - network/parse: can't verify, don't fail
        r.warn("telegram token (live)", f"couldn't reach Telegram: {exc}")
        return
    if data.get("ok"):
        username = (data.get("result") or {}).get("username", "?")
        r.ok("telegram token (live)", f"@{username}")
    else:
        r.fail(
            "telegram token (live)",
            f"Telegram rejected the token: {data.get('description', 'getMe failed')}",
        )


def _check_brain(r: _Report, config: AppConfig, config_path: Path) -> None:
    """If the brain module is enabled, warn when its API key isn't resolvable.

    The key comes from BRAIN_API_KEY (env or the .env sidecar) or, as a fallback,
    a hand-set [modules.brain] api_key. Missing only disables /ask, so it's a WARN.
    """
    if "brain" not in config.enabled_modules:
        return
    from octoops.core.envfile import load_env_file

    env_name = "BRAIN_API_KEY"
    section = config.module_config("brain")
    key = (
        os.environ.get(env_name)
        or load_env_file(config_path.parent / ".env").get(env_name)
        or section.get("api_key")
    )
    model = section.get("model") or "default (google/gemini-2.0-flash-exp:free)"
    if key:
        r.ok("brain", f"API key set; model {model}")
    else:
        r.warn(
            "brain",
            f"enabled but no {env_name} (set it via --setup or the environment); "
            "/ask will refuse until then",
        )


def _check_config(
    r: _Report,
    config_path: Path,
    paths: AppPaths,
    *,
    verify_token: bool = False,
    api_factory: Callable[[str], Any] | None = None,
) -> None:
    if not config_path.is_file():
        r.warn("config.toml", f"not found at {config_path} (run: octoops --setup)")
        return
    try:
        config = AppConfig.load(config_path)
    except OctoOpsError as exc:
        r.fail("config.toml", str(exc))
        return
    r.ok("config.toml", f"loaded ({config_path})")

    # Timezone
    try:
        ZoneInfo(config.core.timezone)
        r.ok("timezone", config.core.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        r.fail("timezone", f"unknown: {config.core.timezone!r}")

    # Log directory writable
    log_path = paths.resolve(config.core.log_file)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        probe = log_path.parent / ".octoops-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        r.ok("log directory writable", str(log_path.parent))
    except OSError as exc:
        r.fail("log directory writable", str(exc))

    # WhatsApp bridge + ports — only relevant when WhatsApp is enabled.
    if not config.transport.whatsapp_enabled:
        r.ok("WhatsApp", "disabled (Telegram-only)")
    else:
        bridge = paths.resolve(config.transport.whatsapp_bridge_path)
        if bridge.is_file():
            r.ok("whatsmeow bridge", str(bridge))
        else:
            r.warn(
                "whatsmeow bridge",
                f"missing at {bridge} (WhatsApp output disabled until present)",
            )

        # Pairing session: the bridge persists its WhatsApp login in whatsmeow.db
        # next to the binary's working directory (OCTOOPS_HOME under the scheduled
        # task). Absence means the QR pairing never happened — WhatsApp will sit
        # offline until someone scans, so say it here instead of at 2am.
        session_db = paths.home / "whatsmeow.db"
        if session_db.is_file():
            r.ok("WhatsApp session", "paired (whatsmeow.db present)")
        else:
            r.warn(
                "WhatsApp session",
                "not paired yet — run --setup (or start the bot interactively) "
                "and scan the QR code",
            )

        for label, port in (
            ("bridge port", config.transport.whatsapp_bridge_port),
            ("callback port", config.transport.octoops_callback_port),
        ):
            if _port_free(port):
                r.ok(f"{label} {port}", "free")
            else:
                r.warn(f"{label} {port}", "in use (another instance running?)")

    # Roles sanity
    if not (
        config.core.admin_user_ids
        or config.core.operator_user_ids
        or config.core.allowed_user_ids
    ):
        r.warn("authorization", "no user IDs configured — nobody can use the bot")

    # Brain module (if enabled) — API key resolvable?
    _check_brain(r, config, config_path)

    # Live token validation (opt-in / networked).
    if verify_token:
        _check_token_live(r, config.telegram.bot_token, api_factory)


def run_checks(
    config_path: str | Path,
    paths: AppPaths | None = None,
    *,
    verify_token: bool = False,
    api_factory: Callable[[str], Any] | None = None,
) -> int:
    config_path = Path(config_path)
    paths = paths or AppPaths.from_config(config_path)

    print("OctoOps diagnostics")
    print(f"  home: {paths.home}")
    print(f"  expected bridge filename: {_bridge_filename()}")
    print("checks:")

    r = _Report()
    _check_python(r)
    _check_deps(r)
    _check_config(r, config_path, paths, verify_token=verify_token, api_factory=api_factory)

    print()
    if r.failed:
        print(f"{_FAIL} FAILED — resolve the items above before starting.")
        return 1
    if r.warned:
        print(f"{_OK} OK with warnings — review the {_WARN} items.")
        return 0
    print(f"{_OK} All checks passed.")
    return 0
