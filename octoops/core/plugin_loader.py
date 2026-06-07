"""Module discovery and loading.

Scans the in-tree modules/ directory (resolved relative to the package, so a
source-checkout deploy can drop a folder in and restart). For each subdir with a
plugin.json whose name is in [modules] enabled, imports the package and calls
load(ctx) -> ModuleRegistration. A broken import or load() is logged and skipped;
the runtime continues.
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from octoops.core.contracts import ModuleRegistration
from octoops.core.errors import ModuleLoadError
from octoops.core.logging import get_logger
from octoops.core.registry import ModuleConfig, ModuleContext, Registry

_log = get_logger("octoops.core.plugin_loader")

# octoops/core/plugin_loader.py -> parent.parent == octoops/  ->  octoops/modules
MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"

# The Python import path corresponding to MODULES_DIR.
_MODULES_PKG = "octoops.modules"


@dataclass
class LoadedModule:
    registration: ModuleRegistration
    ctx: ModuleContext


@dataclass
class Manifest:
    name: str
    version: str
    description: str


@dataclass
class DiscoveredModule:
    """A module found by the wizard pre-scan, with its declared metadata."""

    manifest: Manifest
    registration: ModuleRegistration | None = None
    error: str | None = None


def _stub_registry() -> Registry:
    """A minimal, non-functional Registry for introspecting modules' load().

    Used only by the wizard pre-scan, before a real config exists. Nothing is
    started; load() is expected to merely return a ModuleRegistration.
    """
    from octoops.core.config import (
        AppConfig,
        CoreConfig,
        TelegramConfig,
        TransportConfig,
    )
    from octoops.core.event_bus import EventBus
    from octoops.core.permissions import Permissions
    from octoops.core.scheduler import Scheduler

    config = AppConfig(
        telegram=TelegramConfig(bot_token="", admin_chat_id=""),
        transport=TransportConfig(),
        core=CoreConfig(timezone="UTC"),
    )
    return Registry(
        config=config,
        event_bus=EventBus(),
        scheduler=Scheduler("UTC"),
        permissions=Permissions(
            allowed_user_ids=[], operator_user_ids=[], admin_user_ids=[]
        ),
        start_time=datetime.now(timezone.utc),
    )


def discover_modules(
    modules_dir: Path | None = None, external_dir: Path | None = None
) -> list[DiscoveredModule]:
    """Discover all modules (regardless of enabled state) and their declarations.

    Imports each module and calls load() with a stub context to collect its
    ModuleRegistration (commands, config_fields). Used by the setup wizard so it
    can render per-module config fields without any module-specific knowledge.
    A module that fails to import or load is returned with an error and no
    registration rather than aborting the scan.
    """
    registry = _stub_registry()
    sources: list[tuple[Path, str | None]] = [(modules_dir or MODULES_DIR, _MODULES_PKG)]
    if external_dir is not None and external_dir.is_dir():
        if external_dir.resolve() != (modules_dir or MODULES_DIR).resolve():
            ext = str(external_dir.resolve())
            if ext not in sys.path:
                sys.path.insert(0, ext)
            sources.append((external_dir, None))

    found: list[DiscoveredModule] = []
    seen: set[str] = set()
    for source_dir, prefix in sources:
        if not source_dir.is_dir():
            continue
        for child in sorted(source_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            manifest_path = child / "plugin.json"
            if not manifest_path.is_file():
                continue
            manifest = _read_manifest(manifest_path)
            if manifest is None or manifest.name in seen:
                continue
            seen.add(manifest.name)
            try:
                import_path = child.name if prefix is None else f"{prefix}.{child.name}"
                mod = importlib.import_module(import_path)
                load_fn = getattr(mod, "load", None)
                if load_fn is None:
                    raise ModuleLoadError("module has no load(ctx) function")
                ctx = ModuleContext(
                    name=manifest.name,
                    config=ModuleConfig({}),
                    registry=registry,
                    event_bus=registry.event_bus,
                    scheduler=registry.scheduler,
                )
                registration = load_fn(ctx)
                if not isinstance(registration, ModuleRegistration):
                    raise ModuleLoadError("load() did not return a ModuleRegistration")
                found.append(DiscoveredModule(manifest=manifest, registration=registration))
            except Exception as exc:  # noqa: BLE001 - report, don't abort the scan
                found.append(
                    DiscoveredModule(manifest=manifest, registration=None, error=str(exc))
                )
    return found


def _read_manifest(path: Path) -> Manifest | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Manifest(
            name=data["name"],
            version=str(data.get("version", "0.0.0")),
            description=str(data.get("description", "")),
        )
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        _log.error("module.manifest_invalid", path=str(path), error=str(exc))
        return None


def load_modules(
    registry: Registry, modules_dir: Path | None = None
) -> list[LoadedModule]:
    """Discover and load enabled modules. Never raises for a single bad module.

    Scans the built-in packaged ``octoops/modules/`` directory, then (if the
    install defines a base dir) an external ``$OCTOOPS_HOME/modules/`` directory
    so operators can drop modules in next to config.toml without touching the
    checkout. A name already loaded from a built-in is not overridden.
    """
    enabled = set(registry.config.enabled_modules)
    loaded: list[LoadedModule] = []
    seen: set[str] = set()

    # (directory, import_prefix). import_prefix=None -> top-level import after
    # the directory is added to sys.path (external drop-in modules).
    sources: list[tuple[Path, str | None]] = [(modules_dir or MODULES_DIR, _MODULES_PKG)]
    if modules_dir is None and registry.paths is not None:
        external = registry.paths.modules
        if external.is_dir() and external.resolve() != MODULES_DIR.resolve():
            external_str = str(external.resolve())
            if external_str not in sys.path:
                sys.path.insert(0, external_str)
            sources.append((external, None))

    for source_dir, prefix in sources:
        if not source_dir.is_dir():
            _log.warning("loader.skip", reason="dir_missing", path=str(source_dir))
            continue

        for child in sorted(source_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            manifest_path = child / "plugin.json"
            if not manifest_path.is_file():
                continue

            manifest = _read_manifest(manifest_path)
            if manifest is None:
                continue

            if manifest.name not in enabled:
                _log.info("module.skipped", module=manifest.name, reason="not_enabled")
                continue

            if manifest.name in seen:
                _log.warning(
                    "module.shadowed", module=manifest.name, path=str(child)
                )
                continue

            try:
                import_path = child.name if prefix is None else f"{prefix}.{child.name}"
                mod = importlib.import_module(import_path)
                load_fn = getattr(mod, "load", None)
                if load_fn is None:
                    raise ModuleLoadError("module has no load(ctx) function")

                ctx = ModuleContext(
                    name=manifest.name,
                    config=ModuleConfig(registry.config.module_config(manifest.name)),
                    registry=registry,
                    event_bus=registry.event_bus,
                    scheduler=registry.scheduler,
                )
                registration = load_fn(ctx)
                if not isinstance(registration, ModuleRegistration):
                    raise ModuleLoadError(
                        "load() did not return a ModuleRegistration instance"
                    )

                loaded.append(LoadedModule(registration=registration, ctx=ctx))
                seen.add(manifest.name)
                _log.info(
                    "module.loaded",
                    module=manifest.name,
                    version=manifest.version,
                    source="external" if prefix is None else "builtin",
                    commands=len(registration.commands),
                    jobs=len(registration.jobs),
                    listeners=len(registration.listeners),
                )
            except Exception as exc:  # noqa: BLE001 - one bad module must not abort load
                _log.error(
                    "module.load_failed",
                    module=manifest.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                continue

    return loaded
