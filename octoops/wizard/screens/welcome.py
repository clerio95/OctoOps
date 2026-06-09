from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from octoops.wizard.screens.base import BaseStep


class WelcomeStep(BaseStep):
    STEP_ID = "welcome"
    title_key = "welcome.title"
    next_key = "nav.begin"
    # Back returns to the language picker so a wrong language can be corrected.

    def content(self) -> ComposeResult:
        yield Static(self.tr("welcome.intro"))
        if self.wizard_app.config_exists:
            yield Static(self.tr("welcome.existing"), classes="warn")
