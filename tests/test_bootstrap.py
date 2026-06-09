"""Bootstrap failure isolation: one bad module/job must never brick the bot."""

from __future__ import annotations

import pytest

from octoops.core.bootstrap import Runtime, _register_modules, start_runtime, stop_runtime
from octoops.core.contracts import CommandDef, JobDef, ModuleRegistration
from octoops.core.plugin_loader import LoadedModule
from octoops.core.registry import ModuleConfig, ModuleContext
from octoops.core.router import Router
from octoops.shared.models import Response, Role


async def _noop_handler(request, ctx):
    return Response(text="ok", chat_id=request.chat_id)


async def _noop_job(ctx):
    pass


def _module(registry, name, commands=(), jobs=()):
    ctx = ModuleContext(
        name=name,
        config=ModuleConfig({}),
        registry=registry,
        event_bus=registry.event_bus,
        scheduler=registry.scheduler,
    )
    reg = ModuleRegistration(
        name=name,
        commands=[
            CommandDef(c, f"{c} cmd", Role.Viewer, _noop_handler) for c in commands
        ],
        jobs=list(jobs),
    )
    return LoadedModule(registration=reg, ctx=ctx)


# --- duplicate command -> module disabled, not fatal ---------------------------


def test_colliding_module_is_disabled_not_fatal(registry):
    router = Router(registry.permissions)
    first = _module(registry, "alpha", commands=["ping", "report"])
    second = _module(registry, "beta", commands=["beta_only", "report"])  # collides

    active = _register_modules([first, second], router, registry.event_bus, registry)

    assert [m.registration.name for m in active] == ["alpha"]
    # The survivor keeps its commands; the loser is fully rolled back — including
    # the command it registered successfully before the collision.
    assert router.has_command("ping") and router.has_command("report")
    assert not router.has_command("beta_only")
    # The disable is operator-visible, not just a log line.
    assert any("beta" in err and "report" in err for err in registry.module_errors)


def test_disabled_module_listed_in_status_text(registry):
    from octoops.modules.status import build_status_text

    registry.module_errors.append("beta: duplicate command 'report'")
    text = build_status_text(registry)
    assert "disabled: beta" in text


def test_no_collision_keeps_all_modules(registry):
    router = Router(registry.permissions)
    mods = [
        _module(registry, "alpha", commands=["ping"]),
        _module(registry, "beta", commands=["pong"]),
    ]
    active = _register_modules(mods, router, registry.event_bus, registry)
    assert len(active) == 2
    assert registry.module_errors == []


# --- bad cron schedule -> job skipped, not fatal --------------------------------


@pytest.mark.asyncio
async def test_bad_cron_schedule_is_skipped_not_fatal(registry):
    router = Router(registry.permissions)
    loaded = _module(
        registry,
        "jobs",
        jobs=[
            JobDef(name="bad", schedule="0 9 * *", handler=_noop_job),  # 4 fields
            JobDef(name="good", schedule="0 9 * * *", handler=_noop_job),
        ],
    )
    runtime = Runtime(
        config=registry.config, registry=registry, router=router, modules=[loaded]
    )

    await start_runtime(runtime)  # must not raise
    try:
        job_ids = [j.id for j in registry.scheduler._scheduler.get_jobs()]
        assert "jobs:good" in job_ids
        assert "jobs:bad" not in job_ids
    finally:
        await stop_runtime(runtime)
