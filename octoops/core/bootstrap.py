"""Startup/shutdown sequence.

build_runtime performs the synchronous wiring (load config -> registry -> load
modules -> register commands/listeners/jobs). start_runtime runs on_startup hooks
then starts the scheduler. stop_runtime runs on_shutdown hooks then stops the
scheduler. The async serve loop (transports + signal handling) lives in __main__.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from octoops.core.config import AppConfig
from octoops.core.event_bus import EventBus
from octoops.core.invites import InviteStore
from octoops.core.logging import get_logger
from octoops.core.paths import AppPaths
from octoops.core.permissions import Permissions
from octoops.core.plugin_loader import LoadedModule, load_modules
from octoops.core.registry import Registry
from octoops.core.errors import RouterError
from octoops.core.role_store import RoleStore
from octoops.core.router import Router
from octoops.core.scheduler import Scheduler

_log = get_logger("octoops.core.bootstrap")


@dataclass
class Runtime:
    config: AppConfig
    registry: Registry
    router: Router
    modules: list[LoadedModule] = field(default_factory=list)


def build_runtime(config: AppConfig, paths: AppPaths | None = None) -> Runtime:
    """Steps 3-8: build registry, load modules, register commands/listeners/jobs."""
    app_paths = paths or AppPaths.from_config()
    # Runtime grants (added via the access module) layer on top of the config base.
    role_store = RoleStore(app_paths.data / "access.json")
    permissions = Permissions(
        allowed_user_ids=config.core.allowed_user_ids,
        operator_user_ids=config.core.operator_user_ids,
        admin_user_ids=config.core.admin_user_ids,
        default_role=config.core.default_role,
        store=role_store,
        runtime_grants=role_store.load(),
    )
    event_bus = EventBus()
    scheduler = Scheduler(timezone=config.core.timezone)
    registry = Registry(
        config=config,
        event_bus=event_bus,
        scheduler=scheduler,
        permissions=permissions,
        start_time=datetime.now(ZoneInfo(config.core.timezone)),
        paths=app_paths,
        invites=InviteStore(app_paths.data / "invites.json"),
    )
    router = Router(permissions)
    registry.router = router  # let modules introspect commands (e.g. /help)

    modules = _register_modules(load_modules(registry), router, event_bus, registry)
    registry.module_names = [m.registration.name for m in modules]

    _log.info(
        "runtime.built",
        modules=len(modules),
        commands=len(router.commands()),
    )
    return Runtime(config=config, registry=registry, router=router, modules=modules)


def _register_modules(
    modules: list[LoadedModule],
    router: Router,
    event_bus: EventBus,
    registry: Registry,
) -> list[LoadedModule]:
    """Register each module's commands and listeners, isolating failures.

    A command-name collision (RouterError) disables the *colliding module* — its
    already-registered commands are rolled back, it is excluded from the runtime
    (no listeners, jobs, or hooks), and the reason lands in registry.module_errors
    so /status can show it. One bad drop-in module must never brick the bot.
    """
    active: list[LoadedModule] = []
    for loaded in modules:
        reg, ctx = loaded.registration, loaded.ctx
        registered: list[str] = []
        try:
            for command in reg.commands:
                router.register(command, ctx)
                registered.append(command.name)
        except RouterError as exc:
            for name in registered:
                router.unregister(name)
            registry.module_errors.append(f"{ctx.name}: {exc}")
            _log.error("module.disabled", module=ctx.name, error=str(exc))
            continue
        for listener in reg.listeners:
            event_bus.subscribe(listener.event, listener.handler, ctx)
        active.append(loaded)
    return active


async def start_runtime(runtime: Runtime) -> None:
    """Steps 9-10: register jobs, run on_startup hooks, start the scheduler."""
    for loaded in runtime.modules:
        reg, ctx = loaded.registration, loaded.ctx
        for job in reg.jobs:
            try:
                await runtime.registry.scheduler.add_job(job, ctx)
            except Exception as exc:  # noqa: BLE001 - a bad schedule (e.g. a cron
                # typo raising ValueError) must disable that job, not kill the bot.
                _log.error(
                    "job.register_failed",
                    module=reg.name,
                    job=job.name,
                    schedule=job.schedule,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    for loaded in runtime.modules:
        reg, ctx = loaded.registration, loaded.ctx
        if reg.on_startup is not None:
            try:
                await reg.on_startup(ctx)
            except Exception as exc:  # noqa: BLE001 - hook failure must not abort startup
                _log.error(
                    "module.on_startup_failed",
                    module=reg.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    await runtime.registry.scheduler.start()
    _log.info("runtime.started")


async def stop_runtime(runtime: Runtime) -> None:
    """Shutdown: on_shutdown hooks -> stop scheduler -> drain event bus."""
    for loaded in reversed(runtime.modules):
        reg, ctx = loaded.registration, loaded.ctx
        if reg.on_shutdown is not None:
            try:
                await reg.on_shutdown(ctx)
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "module.on_shutdown_failed",
                    module=reg.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    await runtime.registry.scheduler.shutdown()
    await runtime.registry.event_bus.drain()
    _log.info("runtime.stopped")
