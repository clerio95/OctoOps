from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from octoops.wizard.screens.base import BaseStep


class WelcomeStep(BaseStep):
    STEP_ID = "welcome"
    step_title = "🐙 Welcome to OctoOps setup"
    show_back = False
    next_label = "Begin"

    def content(self) -> ComposeResult:
        yield Static(
            "This wizard writes config.toml.\n\n"
            "You'll set up Telegram (control plane), the WhatsApp bridge "
            "(output), core settings, and which modules are enabled."
        )
        if self.wizard_app.config_exists:
            yield Static(
                "\n⚠ An existing config.toml was found. Its current values are "
                "pre-filled below — review and adjust as needed. Finishing "
                "overwrites the file (a timestamped .bak backup is saved first).",
                classes="warn",
            )
