"""Setup wizard entry point.

Discovers modules (to harvest their config fields), pre-fills from any existing
config, runs the Textual wizard, backs up and writes config.toml, then optionally
registers the Windows Task Scheduler task and runs the WhatsApp QR pairing flow.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from datetime import datetime
from pathlib import Path

from octoops.core.config import AppConfig
from octoops.core.errors import OctoOpsError
from octoops.core.paths import AppPaths
from octoops.core.plugin_loader import discover_modules
from octoops.wizard.state import WizardState


def run_wizard(config_path: str, paths: AppPaths | None = None) -> bool:
    """Run the interactive setup. Returns True if config.toml was written."""
    config_path = Path(config_path)
    paths = paths or AppPaths.from_config(config_path)

    external = paths.modules if paths.modules.is_dir() else None
    discovered = discover_modules(external_dir=external)

    existing = _load_existing_state(config_path)

    # Imported here so importing the package doesn't require Textual to be loaded.
    from octoops.wizard.app import WizardApp

    app = WizardApp(
        discovered=discovered,
        config_exists=config_path.is_file(),
        state=existing,
    )
    state = app.run()
    if state is None:
        return False

    from octoops.wizard.writer import write_config, write_env

    _backup_existing(config_path)
    write_config(state, config_path)
    print(f"Wrote {config_path}")
    if write_env(state, config_path.parent / ".env") is not None:
        print(f"Wrote {config_path.parent / '.env'} (module secrets, private 0600)")

    _maybe_register_task(state, paths)
    _maybe_pair_whatsapp(state, paths)
    return True


def _load_existing_state(config_path: Path) -> WizardState | None:
    """Pre-fill the wizard from an existing config so re-running --setup edits it
    rather than starting blank. A missing or unparseable config starts fresh."""
    if not config_path.is_file():
        return None
    from octoops.wizard.writer import state_from_config

    try:
        config = AppConfig.load(config_path)
    except OctoOpsError as exc:
        print(f"Existing {config_path} couldn't be parsed ({exc}); starting fresh.")
        return None
    print(f"Loaded existing {config_path} — current values are pre-filled.")
    state = state_from_config(config)
    # Pre-fill secrets from the existing .env so a re-run doesn't blank them.
    from octoops.core.envfile import load_env_file

    state.secrets.update(load_env_file(config_path.parent / ".env"))
    return state


def _backup_existing(config_path: Path) -> None:
    """Copy the current config aside before overwriting, so a re-run can never
    lose the previous settings. Best-effort: a backup failure must not block setup."""
    if not config_path.is_file():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = config_path.with_name(f"{config_path.name}.{stamp}.bak")
    try:
        shutil.copy2(config_path, backup)
        print(f"Backed up previous config to {backup}")
    except OSError as exc:
        print(f"Could not back up {config_path} ({exc}); continuing.")


def _maybe_register_task(state, paths: AppPaths) -> None:
    if not state.register_task:
        return
    from octoops.wizard.task_scheduler import register_task

    ok, message = register_task(sys.executable, str(paths.home))
    print(message)


def _maybe_pair_whatsapp(state, paths: AppPaths) -> None:
    if not state.use_whatsapp:
        return
    bridge = paths.resolve(state.whatsapp_bridge_path)
    if not bridge.exists():
        print(
            f"WhatsApp bridge not found at {bridge}; skipping pairing.\n"
            "Run the bot once the bridge binary is in place to pair."
        )
        return
    try:
        from octoops.wizard.pairing import run_pairing

        asyncio.run(run_pairing(str(bridge), state.whatsapp_bridge_port))
    except Exception as exc:  # noqa: BLE001 - pairing is best-effort
        print(f"Pairing skipped due to error: {exc}")
