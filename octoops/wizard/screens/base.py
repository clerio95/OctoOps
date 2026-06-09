"""BaseStep — shared scaffolding for every wizard screen.

Each step renders a title, its own body widgets (``content``), an error line, and
Back / Next / Cancel buttons. Next runs the step's ``save`` (validate + write into
the shared WizardState); on success the app advances to the next applicable step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from octoops.wizard.i18n import translate

if TYPE_CHECKING:
    from octoops.wizard.app import WizardApp
    from octoops.wizard.state import WizardState


class BaseStep(Screen):
    STEP_ID: str = ""
    # Catalog keys (resolved against the wizard's chosen language at compose time)
    # rather than literal text, so every screen renders in English or PT-BR.
    title_key: str = "nav.next"  # overridden by every concrete step
    next_key: str = "nav.next"
    show_back: bool = True

    @property
    def state(self) -> "WizardState":
        return self.wizard_app.state

    @property
    def wizard_app(self) -> "WizardApp":
        return self.app  # type: ignore[return-value]

    @property
    def lang(self) -> str:
        return self.wizard_app.language

    def tr(self, key: str, **kwargs: object) -> str:
        """Translate ``key`` into the wizard's current language."""
        return translate(key, self.lang, **kwargs)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="body"):
            yield Static(self.tr(self.title_key), classes="step-title")
            yield from self.content()
            yield Static("", id="error", classes="error")
        with Horizontal(id="nav"):
            if self.show_back:
                yield Button(self.tr("nav.back"), id="back")
            yield Button(self.tr(self.next_key), id="next", variant="primary")
            yield Button(self.tr("nav.cancel"), id="cancel", variant="error")
        yield Footer()

    def content(self) -> ComposeResult:  # overridden by subclasses
        return iter(())

    def save(self) -> str | None:
        """Validate inputs and write to state. Return an error string, or None."""
        return None

    def _set_error(self, message: str) -> None:
        self.query_one("#error", Static).update(f"⚠ {message}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "cancel":
            self.wizard_app.exit(None)
        elif bid == "back":
            self.wizard_app.go_back()
        elif bid == "next":
            error = self.save()
            if error:
                self._set_error(error)
            else:
                self.wizard_app.go_next(self.STEP_ID)
