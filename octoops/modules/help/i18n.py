"""Localized framing for the help module (English + Brazilian Portuguese).

Only the *framing* (title, the empty-list line, the help command's own
description) is localized; individual command descriptions are shown exactly as
each module authored them. The active language is ``core.language``.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

_CATALOG: dict[str, dict[str, str]] = {
    "display": {"en": "Help", "pt-BR": "Ajuda"},
    "desc": {
        "en": "Show the list of commands you can use.",
        "pt-BR": "Mostrar a lista de comandos que você pode usar.",
    },
    "header": {
        "en": "🐙 OctoOps — commands you can use:",
        "pt-BR": "🐙 OctoOps — comandos que você pode usar:",
    },
    "none": {
        "en": "No commands are available for your role.",
        "pt-BR": "Nenhum comando disponível para o seu papel.",
    },
}


def normalize_lang(lang: str) -> str:
    return "pt-BR" if (lang or "").strip().lower().startswith("pt") else "en"


def tr(lang: str, key: str, /, **kwargs: object) -> str:
    code = normalize_lang(lang)
    entry = _CATALOG.get(key)
    text = key if entry is None else (entry.get(code) or entry.get(DEFAULT_LANGUAGE) or key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
