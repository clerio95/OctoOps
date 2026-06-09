from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import SelectionList, Static
from textual.widgets.selection_list import Selection

from octoops.wizard.screens.base import BaseStep


class ModulesStep(BaseStep):
    STEP_ID = "modules"
    title_key = "modules.title"

    def content(self) -> ComposeResult:
        loadable = [m for m in self.wizard_app.discovered if m.registration is not None]
        if not loadable:
            yield Static(self.tr("modules.none"))
            return
        yield Static(self.tr("modules.check"))
        yield Static(self.tr("modules.hint"), classes="warn")
        selections = [
            Selection(
                f"{m.manifest.name} — {m.manifest.description}",
                m.manifest.name,
                m.manifest.name in self.state.enabled_modules,
            )
            for m in loadable
        ]
        yield SelectionList(*selections, id="modules")
        # Surface modules that failed to load so the operator knows.
        for m in self.wizard_app.discovered:
            if m.registration is None:
                yield Static(
                    self.tr("modules.failed", name=m.manifest.name, error=m.error),
                    classes="warn",
                )

    def save(self) -> str | None:
        try:
            selection = self.query_one("#modules", SelectionList)
        except Exception:  # noqa: BLE001 - no modules discovered
            self.state.enabled_modules = []
            return None
        self.state.enabled_modules = list(selection.selected)
        return None
