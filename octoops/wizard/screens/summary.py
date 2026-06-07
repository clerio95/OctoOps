from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from octoops.wizard.screens.base import BaseStep
from octoops.wizard.writer import render_config


class SummaryStep(BaseStep):
    STEP_ID = "summary"
    step_title = "Review & confirm"
    next_label = "Finish"

    def content(self) -> ComposeResult:
        yield Static("This config.toml will be written (secrets hidden below):")
        yield Static(render_config(self.state, redact_secrets=True), classes="preview")

    def save(self) -> str | None:
        # No-op: confirming advances past the last step, which exits with state.
        return None
