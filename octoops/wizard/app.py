"""WizardApp — the Textual application driving the setup screens.

A linear step flow over a screen stack: each step's Next pushes the next
applicable step; Back pops. module_config is shown only if an enabled module
declares config fields; task_scheduler only on Windows. Finishing the last step
exits the app with the populated WizardState (or None if cancelled).
"""

from __future__ import annotations

import sys

from textual import events
from textual.app import App
from textual.screen import Screen
from textual.widgets import Input

from octoops.core.plugin_loader import DiscoveredModule
from octoops.wizard.screens.core_settings import CoreSettingsStep
from octoops.wizard.screens.module_config import ModuleConfigStep
from octoops.wizard.screens.modules import ModulesStep
from octoops.wizard.screens.summary import SummaryStep
from octoops.wizard.screens.task_scheduler import TaskSchedulerStep
from octoops.wizard.screens.telegram import TelegramStep
from octoops.wizard.screens.welcome import WelcomeStep
from octoops.wizard.screens.whatsapp import WhatsAppStep
from octoops.wizard.state import WizardState
from octoops.wizard.task_scheduler import is_windows

_STEP_ORDER = [
    "welcome",
    "telegram",
    "whatsapp",
    "core",
    "modules",
    "module_config",
    "task_scheduler",
    "summary",
]

_FACTORIES = {
    "welcome": WelcomeStep,
    "telegram": TelegramStep,
    "whatsapp": WhatsAppStep,
    "core": CoreSettingsStep,
    "modules": ModulesStep,
    "module_config": ModuleConfigStep,
    "task_scheduler": TaskSchedulerStep,
    "summary": SummaryStep,
}


class WizardApp(App):
    CSS = """
    Screen { align: center top; }
    #body { padding: 1 2; height: 1fr; }
    #nav { height: auto; padding: 0 2 1 2; }
    #nav Button { margin: 0 1 0 0; }
    .step-title { text-style: bold; padding: 1 0; }
    .warn { color: $warning; }
    .error { color: $error; padding: 1 0; }
    .preview { padding: 1; border: round $primary; }
    Label { padding: 1 0 0 0; }
    """
    # ctrl+c is Textual's default quit shortcut; override it here so users can
    # press it in an Input field (e.g. to cancel a selection) without killing
    # the wizard. Escape and the Cancel button remain the exit paths.
    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+c", "noop", "")]

    def __init__(
        self,
        discovered: list[DiscoveredModule],
        config_exists: bool = False,
        state: WizardState | None = None,
    ) -> None:
        super().__init__()
        self.discovered = discovered
        self.config_exists = config_exists
        self.state = state or WizardState()
        if not self.state.enabled_modules:
            # Default-enable everything that loaded cleanly.
            self.state.enabled_modules = [
                m.manifest.name for m in discovered if m.registration is not None
            ]

    def on_mount(self) -> None:
        self.push_screen(WelcomeStep())

    def enabled_with_fields(self) -> list[DiscoveredModule]:
        return [
            m
            for m in self.discovered
            if m.manifest.name in self.state.enabled_modules
            and m.registration is not None
            and m.registration.config_fields
        ]

    def _applicable(self, step_id: str) -> bool:
        if step_id == "module_config":
            return bool(self.enabled_with_fields())
        if step_id == "task_scheduler":
            return is_windows()
        return True

    def _make(self, step_id: str) -> Screen:
        return _FACTORIES[step_id]()

    def go_next(self, current_id: str) -> None:
        start = _STEP_ORDER.index(current_id) + 1
        for step_id in _STEP_ORDER[start:]:
            if self._applicable(step_id):
                self.push_screen(self._make(step_id))
                return
        # Past the last step → finished.
        self.exit(self.state)

    def go_back(self) -> None:
        # Keep the first wizard screen (welcome) as the floor.
        if len(self.screen_stack) > 2:
            self.pop_screen()

    def action_noop(self) -> None:
        pass

    def action_cancel(self) -> None:
        self.exit(None)

    def on_key(self, event: events.Key) -> None:
        if event.key != "ctrl+v":
            return
        focused = self.focused
        if not isinstance(focused, Input):
            return
        text = _clipboard_paste()
        if text:
            focused._insert_text_at_cursor(text)
        event.stop()


def _clipboard_paste() -> str:
    """Read plain text from the Windows clipboard via ctypes (no extra deps).
    Returns an empty string on non-Windows or when the clipboard is empty."""
    if not sys.platform.startswith("win"):
        return ""
    import ctypes

    CF_UNICODETEXT = 13
    try:
        if not ctypes.windll.user32.OpenClipboard(None):
            return ""
        handle = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
        return ctypes.wstring_at(handle) if handle else ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        try:
            ctypes.windll.user32.CloseClipboard()
        except Exception:  # noqa: BLE001
            pass
