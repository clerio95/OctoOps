from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import RadioButton, RadioSet, Static

from octoops.wizard.i18n import LANGUAGES
from octoops.wizard.screens.base import BaseStep


class LanguageStep(BaseStep):
    """First screen: choose the language the rest of the wizard renders in.

    Shown before Welcome. Its own labels are bilingual (the choice hasn't been
    made yet); picking a language sets ``wizard_app.language``, which every
    later screen reads when it composes.
    """

    STEP_ID = "language"
    title_key = "language.title"
    show_back = False

    def content(self) -> ComposeResult:
        yield Static(self.tr("language.help"))
        codes = list(LANGUAGES)
        current = self.wizard_app.language
        yield RadioSet(
            *(
                RadioButton(LANGUAGES[code], value=(code == current))
                for code in codes
            ),
            id="language",
        )

    def save(self) -> str | None:
        codes = list(LANGUAGES)
        idx = self.query_one("#language", RadioSet).pressed_index
        if 0 <= idx < len(codes):
            self.wizard_app.language = codes[idx]
        return None
