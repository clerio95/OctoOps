from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Checkbox, Static

from octoops.wizard.screens.base import BaseStep


class TaskSchedulerStep(BaseStep):
    STEP_ID = "task_scheduler"
    title_key = "task_scheduler.title"

    def content(self) -> ComposeResult:
        yield Static(self.tr("task_scheduler.intro"))
        yield Checkbox(
            self.tr("task_scheduler.checkbox"),
            value=self.state.register_task,
            id="register_task",
        )

    def save(self) -> str | None:
        self.state.register_task = self.query_one("#register_task", Checkbox).value
        return None
