"""Localized strings for the deadlines module (English + Brazilian Portuguese).

The active language comes from ``core.language`` in config.toml (persisted by the
setup wizard). English is the source language and the fallback. Templates use
``str.format`` fields, so callers pass values as keyword arguments to ``tr``.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

_CATALOG: dict[str, dict[str, str]] = {
    # identity (command name + how the module shows up in /status)
    "cmd": {"en": "deadlines", "pt-BR": "vencimentos"},
    "display": {"en": "Deadlines", "pt-BR": "Vencimentos"},
    "cmd_desc": {
        "en": "Check and add deadlines (interactive).",
        "pt-BR": "Consultar e adicionar vencimentos (interativo).",
    },
    # wizard config-field labels
    "cfg.file_label": {"en": "Deadlines file", "pt-BR": "Arquivo de vencimentos"},
    "cfg.file_desc": {
        "en": "JSON file storing the deadlines (base-dir-relative).",
        "pt-BR": "Arquivo JSON que guarda os vencimentos (relativo à pasta base).",
    },
    "cfg.nearest_label": {
        "en": "Nearest window (days)",
        "pt-BR": "Janela de proximidade (dias)",
    },
    "cfg.nearest_desc": {
        "en": "How many days ahead counts as 'nearest'.",
        "pt-BR": "Quantos dias à frente contam como 'próximos'.",
    },
    # menu
    "menu": {
        "en": (
            "📅 Deadlines — choose an option:\n"
            "1️⃣ Nearest ({days} days)\n"
            "2️⃣ All deadlines\n"
            "3️⃣ Add a new deadline\n"
            "4️⃣ Edit a deadline\n\n"
            "Reply 1, 2, 3 or 4 (or 'cancel')."
        ),
        "pt-BR": (
            "📅 Vencimentos — escolha uma opção:\n"
            "1️⃣ Próximos ({days} dias)\n"
            "2️⃣ Todos os vencimentos\n"
            "3️⃣ Adicionar um novo vencimento\n"
            "4️⃣ Editar um vencimento\n\n"
            "Responda 1, 2, 3 ou 4 (ou 'cancelar')."
        ),
    },
    "menu_invalid": {
        "en": "Please reply 1, 2, 3 or 4 (or 'cancel').",
        "pt-BR": "Responda 1, 2, 3 ou 4 (ou 'cancelar').",
    },
    "cancelled": {"en": "Okay, cancelled.", "pt-BR": "Ok, cancelado."},
    "expired": {
        "en": "⏳ That conversation timed out. Send 'deadlines' to start again.",
        "pt-BR": "⏳ A conversa expirou. Envie 'vencimentos' para começar de novo.",
    },
    # add flow prompts
    "ask_desc": {
        "en": "📝 What is the deadline? (description)",
        "pt-BR": "📝 Qual é o vencimento? (descrição)",
    },
    "ask_date": {
        "en": "📅 Due date? (DD/MM/YYYY)",
        "pt-BR": "📅 Data de vencimento? (DD/MM/AAAA)",
    },
    "ask_orgao": {
        "en": "🏛️ Issuing body / category? (or '-' to skip)",
        "pt-BR": "🏛️ Órgão / categoria? (ou '-' para pular)",
    },
    "ask_freq": {
        "en": "📆 Frequency? e.g. Monthly, Yearly (or '-')",
        "pt-BR": "📆 Frequência? ex.: Mensal, Anual (ou '-')",
    },
    "ask_critico": {"en": "🔴 Critical? (yes/no)", "pt-BR": "🔴 Crítico? (sim/não)"},
    "ask_alerta": {
        "en": "⏰ Alert how many days before? (default {days})",
        "pt-BR": "⏰ Alertar quantos dias antes? (padrão {days})",
    },
    "required_desc": {
        "en": "⚠️ A description is required. Try again (or 'cancel').",
        "pt-BR": "⚠️ A descrição é obrigatória. Tente de novo (ou 'cancelar').",
    },
    "invalid_date": {
        "en": "⚠️ I couldn't read that date. Use DD/MM/YYYY, e.g. 25/12/2026.",
        "pt-BR": "⚠️ Não entendi a data. Use DD/MM/AAAA, ex.: 25/12/2026.",
    },
    "saved": {"en": "✅ Saved (#{id}):\n{summary}", "pt-BR": "✅ Salvo (#{id}):\n{summary}"},
    # edit / delete flow
    "edit_empty": {
        "en": "📭 No deadlines to edit yet.",
        "pt-BR": "📭 Nenhum vencimento para editar ainda.",
    },
    "edit_pick": {
        "en": "✏️ Which deadline to edit? Reply with the number:\n{list}",
        "pt-BR": "✏️ Qual vencimento editar? Responda com o número:\n{list}",
    },
    "edit_pick_invalid": {
        "en": "Please reply with a number from the list (or 'cancel').",
        "pt-BR": "Responda com um número da lista (ou 'cancelar').",
    },
    "edit_field": {
        "en": "✏️ Editing #{id} — {desc}\nWhat do you want to change?\n{fields}",
        "pt-BR": "✏️ Editando #{id} — {desc}\nO que você quer alterar?\n{fields}",
    },
    "edit_field_invalid": {
        "en": "Please reply with a field number (or 'cancel').",
        "pt-BR": "Responda com o número de um campo (ou 'cancelar').",
    },
    "updated": {
        "en": "✅ Updated (#{id}):\n{summary}",
        "pt-BR": "✅ Atualizado (#{id}):\n{summary}",
    },
    "not_found": {
        "en": "⚠️ That deadline no longer exists.",
        "pt-BR": "⚠️ Esse vencimento não existe mais.",
    },
    "delete_confirm": {
        "en": "🗑️ Delete #{id} — {desc}? (yes/no)",
        "pt-BR": "🗑️ Excluir #{id} — {desc}? (sim/não)",
    },
    "deleted": {"en": "🗑️ Deleted #{id}.", "pt-BR": "🗑️ Excluído #{id}."},
    "not_deleted": {"en": "Okay, nothing deleted.", "pt-BR": "Ok, nada foi excluído."},
    "field.delete": {
        "en": "Delete this deadline",
        "pt-BR": "Excluir este vencimento",
    },
    # listings
    "none_upcoming": {
        "en": "✅ No deadlines in the next {days} days.",
        "pt-BR": "✅ Sem vencimentos nos próximos {days} dias.",
    },
    "none_all": {
        "en": "📭 No deadlines registered yet.",
        "pt-BR": "📭 Nenhum vencimento cadastrado ainda.",
    },
    "header_upcoming": {
        "en": "📅 Nearest deadlines ({count}):",
        "pt-BR": "📅 Vencimentos próximos ({count}):",
    },
    "header_all": {
        "en": "📋 All deadlines ({count}):",
        "pt-BR": "📋 Todos os vencimentos ({count}):",
    },
    "error": {
        "en": "⚠️ Something went wrong saving that. It has been logged.",
        "pt-BR": "⚠️ Algo deu errado ao salvar. Foi registrado.",
    },
    # date-status suffixes used when rendering an entry
    "status_overdue": {"en": "overdue {n}d", "pt-BR": "vencido há {n}d"},
    "status_today": {"en": "today", "pt-BR": "HOJE"},
    "status_in": {"en": "{n}d", "pt-BR": "{n}d"},
    "no_date": {"en": "no date", "pt-BR": "sem data"},
    # summary labels
    "label.desc": {"en": "Description", "pt-BR": "Descrição"},
    "label.date": {"en": "Due", "pt-BR": "Vencimento"},
    "label.orgao": {"en": "Body", "pt-BR": "Órgão"},
    "label.freq": {"en": "Frequency", "pt-BR": "Frequência"},
    "label.critico": {"en": "Critical", "pt-BR": "Crítico"},
    "label.alerta": {"en": "Alert (days)", "pt-BR": "Alerta (dias)"},
    "yes": {"en": "yes", "pt-BR": "sim"},
    "no": {"en": "no", "pt-BR": "não"},
}


def normalize_lang(lang: str) -> str:
    """Map a config language string to a catalog key ('pt-BR' for any pt*, else 'en')."""
    return "pt-BR" if (lang or "").strip().lower().startswith("pt") else "en"


def tr(lang: str, key: str, /, **kwargs: object) -> str:
    """Resolve ``key`` for ``lang`` (falling back to English then the key)."""
    code = normalize_lang(lang)
    entry = _CATALOG.get(key)
    if entry is None:
        text = key
    else:
        text = entry.get(code) or entry.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
