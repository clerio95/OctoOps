"""Scheduler — thin wrapper over APScheduler 3.x AsyncIOScheduler.

The spec's public surface (add_job / start / shutdown) is preserved; 3.x stable
is used internally because 4.x is still alpha. Every job is wrapped so a failure
is logged and swallowed — the scheduler keeps running.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from octoops.core.contracts import JobDef
from octoops.core.logging import get_logger

if TYPE_CHECKING:
    from octoops.core.registry import ModuleContext

_log = get_logger("octoops.core.scheduler")


class Scheduler:
    def __init__(self, timezone: str) -> None:
        self._timezone = timezone
        self._scheduler = AsyncIOScheduler(timezone=timezone)

    async def add_job(self, job: JobDef, ctx: "ModuleContext") -> None:
        module = ctx.name
        job_id = f"{module}:{job.name}"

        async def _runner() -> None:
            _log.info("job.started", module=module, job=job.name)
            try:
                await job.handler(ctx)
                _log.info("job.completed", module=module, job=job.name)
            except Exception as exc:  # noqa: BLE001 - boundary: never propagate
                _log.error(
                    "job.failed",
                    module=module,
                    job=job.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        trigger = CronTrigger.from_crontab(job.schedule, timezone=self._timezone)
        self._scheduler.add_job(_runner, trigger=trigger, id=job_id, replace_existing=True)
        _log.info("job.registered", module=module, job=job.name, schedule=job.schedule)

    async def start(self) -> None:
        # AsyncIOScheduler.start() is synchronous but requires a running loop.
        self._scheduler.start()
        _log.info("scheduler.started", timezone=self._timezone)

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            _log.info("scheduler.shutdown")
