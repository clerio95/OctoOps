from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Checkbox, Static

from octoops.wizard.screens.base import BaseStep


class TaskSchedulerStep(BaseStep):
    STEP_ID = "task_scheduler"
    step_title = "Windows Task Scheduler"

    def content(self) -> ComposeResult:
        yield Static(
            "Register OctoOps to start automatically at boot, running as SYSTEM, "
            "restarting on failure (3x / 1 min)?"
        )
        yield Checkbox(
            "Register the boot task", value=self.state.register_task, id="register_task"
        )

    def save(self) -> str | None:
        self.state.register_task = self.query_one("#register_task", Checkbox).value
        return None
