from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from octoops.wizard.screens.base import BaseStep
from octoops.wizard.writer import render_config


class SummaryStep(BaseStep):
    STEP_ID = "summary"
    title_key = "summary.title"
    next_key = "nav.finish"

    def content(self) -> ComposeResult:
        yield Static(self.tr("summary.intro"))
        yield Static(render_config(self.state, redact_secrets=True), classes="preview")

    def save(self) -> str | None:
        # No-op: confirming advances past the last step, which exits with state.
        return None
