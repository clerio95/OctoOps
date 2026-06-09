"""deadlines module — an interactive deadline tracker (pt-BR: "Vencimentos").

Two command names are registered, ``/deadlines`` and ``/vencimentos`` (same
handler), so it works in either language; ``registration.name`` and all replies
localize via ``core.language``. The command opens a menu:

    1) nearest deadlines (a configurable window, default 15 days)
    2) all deadlines
    3) add a new deadline (asks one field at a time)
    4) edit a deadline (pick one, change a field, or delete it)

The flow is a per-user state machine driven by the core ConversationStore; each
reply advances it. On Telegram the user types the command; on WhatsApp inbound,
where every message is forced to one command, a *fresh* message must start with
the keyword ("vencimentos"/"deadlines") to open the menu — otherwise the bot
stays quiet (the handler returns None: no reply). An in-progress conversation
always consumes the next message. A flow that times out (10-min TTL) gets one
"conversation expired" notice on the user's next stale reply, on both transports,
instead of silence.

Records live in a JSON file (see storage.py). Respects the module contract:
declares only via load(), touches only ctx, imports no other module, never
raises out of the handler.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from octoops.core.contracts import (
    CommandDef,
    ConfigField,
    ConfigFieldKind,
    ModuleRegistration,
)
from octoops.core.conversations import conversation_key
from octoops.core.logging import get_logger
from octoops.core.registry import ModuleContext
from octoops.shared.models import Request, Response, Role, TransportSource

from .i18n import tr
from .storage import (
    add_deadline,
    all_sorted,
    delete_deadline,
    find_deadline,
    format_date,
    load_deadlines,
    parse_date,
    to_int,
    upcoming,
    update_deadline,
)

log = get_logger("octoops.modules.deadlines")

DEFAULT_FILE = "data/deadlines.json"
DEFAULT_NEAREST_DAYS = 15
_REPLY_LIMIT = 3900  # stay under Telegram's ~4096 cap

# Conversation steps.
_MENU = "menu"
_DESC = "desc"
_DATE = "date"
_ORGAO = "orgao"
_FREQ = "freq"
_CRITICO = "critico"
_ALERTA = "alerta"
_EDIT_PICK = "edit_pick"
_EDIT_FIELD = "edit_field"
_EDIT_VALUE = "edit_value"
_DELETE_CONFIRM = "delete_confirm"

_CANCEL_WORDS = {"cancel", "cancelar", "/cancel", "sair", "cancela"}
_YES_WORDS = {"yes", "y", "sim", "s", "true", "1"}
_SKIP = {"-", ""}
# A fresh WhatsApp message must start with one of these to open the menu.
_TRIGGER_WORDS = {"vencimentos", "deadlines"}

# Edit field menu: choice number -> stored key.
_EDIT_FIELDS = {
    "1": ("DESCRICAO", "label.desc"),
    "2": ("PROXIMA_DATA", "label.date"),
    "3": ("ORGAO", "label.orgao"),
    "4": ("FREQUENCIA", "label.freq"),
    "5": ("CRITICO", "label.critico"),
    "6": ("ALERTA_DIAS", "label.alerta"),
}
_DELETE_CHOICE = "7"


def _lang(ctx: ModuleContext) -> str:
    return ctx.registry.config.core.language


def _file_path(ctx: ModuleContext) -> Path:
    raw = ctx.config.get("file_path") or DEFAULT_FILE
    paths = ctx.registry.paths
    return paths.resolve(raw) if paths is not None else Path(raw)


def _nearest_days(ctx: ModuleContext) -> int:
    return to_int(ctx.config.get("nearest_days"), DEFAULT_NEAREST_DAYS)


def _is_trigger(text: str) -> bool:
    first = text.strip().lstrip("/").split()
    return bool(first) and first[0].lower() in _TRIGGER_WORDS


def load(ctx: ModuleContext) -> ModuleRegistration:
    lang = _lang(ctx)
    desc = tr(lang, "cmd_desc")
    # Both names always registered (like /help + /ajuda) so the command works in
    # either language and the WhatsApp keyword always resolves.
    return ModuleRegistration(
        name=tr(lang, "display"),
        commands=[
            # whatsapp_keywords let the transport route "deadlines …" /
            # "vencimentos …" here even when another module (e.g. brain) is the
            # configured default inbound command.
            CommandDef(
                "deadlines", desc, Role.Operator, handle,
                whatsapp_keywords=["deadlines"],
            ),
            CommandDef(
                "vencimentos", desc, Role.Operator, handle,
                whatsapp_keywords=["vencimentos"],
            ),
        ],
        config_fields=[
            ConfigField(
                key="file_path",
                label=tr(lang, "cfg.file_label"),
                description=tr(lang, "cfg.file_desc"),
                required=False,
                default=DEFAULT_FILE,
                kind=ConfigFieldKind.FilePath,
            ),
            ConfigField(
                key="nearest_days",
                label=tr(lang, "cfg.nearest_label"),
                description=tr(lang, "cfg.nearest_desc"),
                required=False,
                default=str(DEFAULT_NEAREST_DAYS),
                kind=ConfigFieldKind.Integer,
            ),
        ],
    )


# --- rendering ---------------------------------------------------------------


def _status(lang: str, remaining: int | None) -> str:
    if remaining is None:
        return tr(lang, "no_date")
    if remaining < 0:
        return tr(lang, "status_overdue", n=abs(remaining))
    if remaining == 0:
        return tr(lang, "status_today")
    return tr(lang, "status_in", n=remaining)


def _entry(lang: str, row: dict) -> str:
    due = row.get("_date")
    remaining = row.get("_remaining")
    date_str = format_date(due) if isinstance(due, date) else "--/--/----"
    critical = row.get("CRITICO", "").upper() == "SIM"
    hot = critical or (remaining is not None and 0 <= remaining <= 7)
    emoji = "🔴" if hot else "🟡"
    desc = row.get("DESCRICAO") or "-"
    line = f"{emoji} {date_str} ({_status(lang, remaining)}) — {desc}"
    extras = []
    if row.get("ORGAO"):
        extras.append(f"🏛️ {row['ORGAO']}")
    if row.get("FREQUENCIA"):
        extras.append(f"📆 {row['FREQUENCIA']}")
    if extras:
        line += "\n   " + " · ".join(extras)
    return line


def _render_list(lang: str, header: str, rows: list[dict]) -> str:
    body = header
    for row in rows:
        entry = "\n" + _entry(lang, row)
        if len(body) + len(entry) > _REPLY_LIMIT:
            body += "\n…"
            break
        body += entry
    return body


def _summary(lang: str, record: dict) -> str:
    lines = [f"📝 {record.get('DESCRICAO', '-')}"]
    lines.append(f"{tr(lang, 'label.date')}: {record.get('PROXIMA_DATA', '-')}")
    if record.get("ORGAO"):
        lines.append(f"{tr(lang, 'label.orgao')}: {record['ORGAO']}")
    if record.get("FREQUENCIA"):
        lines.append(f"{tr(lang, 'label.freq')}: {record['FREQUENCIA']}")
    crit = tr(lang, "yes") if record.get("CRITICO") == "SIM" else tr(lang, "no")
    lines.append(f"{tr(lang, 'label.critico')}: {crit}")
    lines.append(f"{tr(lang, 'label.alerta')}: {record.get('ALERTA_DIAS', '-')}")
    return "\n".join(lines)


def _field_menu(lang: str) -> str:
    lines = [f"{n}) {tr(lang, key)}" for n, (_field, key) in _EDIT_FIELDS.items()]
    lines.append(f"{_DELETE_CHOICE}) {tr(lang, 'field.delete')}")
    return "\n".join(lines)


# --- listing actions ---------------------------------------------------------


def _show_nearest(ctx: ModuleContext, lang: str) -> str:
    days = _nearest_days(ctx)
    rows = upcoming(load_deadlines(_file_path(ctx)), days, date.today())
    if not rows:
        return tr(lang, "none_upcoming", days=days)
    return _render_list(lang, tr(lang, "header_upcoming", count=len(rows)), rows)


def _show_all(ctx: ModuleContext, lang: str) -> str:
    rows = all_sorted(load_deadlines(_file_path(ctx)), date.today())
    if not rows:
        return tr(lang, "none_all")
    return _render_list(lang, tr(lang, "header_all", count=len(rows)), rows)


# --- conversation handler ----------------------------------------------------


async def handle(request: Request, ctx: ModuleContext) -> Response | None:
    lang = _lang(ctx)
    store = ctx.registry.conversations
    key = conversation_key(request.source, request.user_id)
    text = " ".join(request.args).strip()

    def reply(body: str) -> Response:
        return Response(text=body, chat_id=request.chat_id)

    # A cancel keyword aborts any open flow from anywhere.
    if text.lower() in _CANCEL_WORDS:
        store.end(key)
        return reply(tr(lang, "cancelled"))

    conv = store.get(key)
    if conv is None:
        # If a flow just timed out, the user gets exactly one notice saying so
        # (consuming the tombstone) instead of their stale reply vanishing.
        expired = store.pop_expired(key) is not None
        if request.source is TransportSource.WhatsApp:
            # Every WhatsApp message is forced to this command, so a fresh one
            # must carry the trigger keyword to open the menu; anything else is
            # not for us — explain a timeout once, otherwise stay silent
            # (returning None sends nothing).
            if not _is_trigger(text):
                return reply(tr(lang, "expired")) if expired else None
        elif expired and not request.raw_text.lstrip().startswith("/"):
            # Telegram forwarded a stale reply to us (see the adapter); a real
            # /command after a timeout just opens the menu as usual.
            return reply(tr(lang, "expired"))
        store.start(key, command=request.command, data={"step": _MENU})
        return reply(tr(lang, "menu", days=_nearest_days(ctx)))

    store.touch(key)
    step = conv.data.get("step", _MENU)

    if step == _MENU:
        return _handle_menu(ctx, lang, store, key, conv, text, reply)
    if step in (_DESC, _DATE, _ORGAO, _FREQ, _CRITICO, _ALERTA):
        return _handle_add(ctx, lang, store, key, conv, step, text, reply)
    return _handle_edit(ctx, lang, store, key, conv, step, text, reply)


def _handle_menu(ctx, lang, store, key, conv, text, reply) -> Response:
    if text == "1":
        store.end(key)
        return reply(_show_nearest(ctx, lang))
    if text == "2":
        store.end(key)
        return reply(_show_all(ctx, lang))
    if text == "3":
        conv.data["step"] = _DESC
        conv.data["record"] = {}
        return reply(tr(lang, "ask_desc"))
    if text == "4":
        return _start_edit(ctx, lang, store, key, conv, reply)
    return reply(tr(lang, "menu_invalid"))


def _handle_add(ctx, lang, store, key, conv, step, text, reply) -> Response:
    record: dict = conv.data.setdefault("record", {})

    if step == _DESC:
        if not text:
            return reply(tr(lang, "required_desc"))
        record["DESCRICAO"] = text
        conv.data["step"] = _DATE
        return reply(tr(lang, "ask_date"))

    if step == _DATE:
        parsed = parse_date(text)
        if parsed is None:
            return reply(tr(lang, "invalid_date"))
        record["PROXIMA_DATA"] = format_date(parsed)
        conv.data["step"] = _ORGAO
        return reply(tr(lang, "ask_orgao"))

    if step == _ORGAO:
        if text not in _SKIP:
            record["ORGAO"] = text
        conv.data["step"] = _FREQ
        return reply(tr(lang, "ask_freq"))

    if step == _FREQ:
        if text not in _SKIP:
            record["FREQUENCIA"] = text
        conv.data["step"] = _CRITICO
        return reply(tr(lang, "ask_critico"))

    if step == _CRITICO:
        record["CRITICO"] = "SIM" if text.lower() in _YES_WORDS else "NAO"
        conv.data["step"] = _ALERTA
        return reply(tr(lang, "ask_alerta", days=_nearest_days(ctx)))

    # _ALERTA — final add step.
    record["ALERTA_DIAS"] = str(to_int(text, _nearest_days(ctx)))
    store.end(key)
    try:
        saved = add_deadline(_file_path(ctx), record)
    except OSError as exc:
        log.error("deadlines.save_failed", error=str(exc))
        return reply(tr(lang, "error"))
    log.info("deadlines.added", id=saved.get("ID"))
    return reply(tr(lang, "saved", id=saved.get("ID", "?"), summary=_summary(lang, saved)))


# --- edit / delete -----------------------------------------------------------


def _start_edit(ctx, lang, store, key, conv, reply) -> Response:
    rows = all_sorted(load_deadlines(_file_path(ctx)), date.today())
    if not rows:
        store.end(key)
        return reply(tr(lang, "edit_empty"))
    ids: list[str] = []
    lines: list[str] = []
    for i, row in enumerate(rows, start=1):
        ids.append(row.get("ID", ""))
        due = row.get("_date")
        date_str = format_date(due) if isinstance(due, date) else tr(lang, "no_date")
        lines.append(f"{i}. {row.get('DESCRICAO') or '-'} — {date_str}")
    conv.data["step"] = _EDIT_PICK
    conv.data["edit_ids"] = ids
    return reply(tr(lang, "edit_pick", list="\n".join(lines)))


def _handle_edit(ctx, lang, store, key, conv, step, text, reply) -> Response:
    if step == _EDIT_PICK:
        ids: list[str] = conv.data.get("edit_ids", [])
        if not text.isdigit() or not (1 <= int(text) <= len(ids)):
            return reply(tr(lang, "edit_pick_invalid"))
        conv.data["edit_id"] = ids[int(text) - 1]
        return _show_field_menu(ctx, lang, conv, reply)

    if step == _EDIT_FIELD:
        if text == _DELETE_CHOICE:
            conv.data["step"] = _DELETE_CONFIRM
            record = find_deadline(load_deadlines(_file_path(ctx)), conv.data.get("edit_id", ""))
            desc = record.get("DESCRICAO", "-") if record else "-"
            return reply(tr(lang, "delete_confirm", id=conv.data.get("edit_id", "?"), desc=desc))
        if text not in _EDIT_FIELDS:
            return reply(tr(lang, "edit_field_invalid"))
        conv.data["edit_field"] = _EDIT_FIELDS[text][0]
        conv.data["step"] = _EDIT_VALUE
        return reply(_prompt_for_field(lang, _EDIT_FIELDS[text][0], ctx))

    if step == _EDIT_VALUE:
        return _apply_edit(ctx, lang, store, key, conv, text, reply)

    # _DELETE_CONFIRM
    store.end(key)
    if text.lower() not in _YES_WORDS:
        return reply(tr(lang, "not_deleted"))
    deadline_id = conv.data.get("edit_id", "")
    try:
        removed = delete_deadline(_file_path(ctx), deadline_id)
    except OSError as exc:
        log.error("deadlines.delete_failed", error=str(exc))
        return reply(tr(lang, "error"))
    if not removed:
        return reply(tr(lang, "not_found"))
    log.info("deadlines.deleted", id=deadline_id)
    return reply(tr(lang, "deleted", id=deadline_id))


def _show_field_menu(ctx, lang, conv, reply) -> Response:
    record = find_deadline(load_deadlines(_file_path(ctx)), conv.data.get("edit_id", ""))
    if record is None:
        return reply(tr(lang, "not_found"))
    conv.data["step"] = _EDIT_FIELD
    return reply(
        tr(
            lang,
            "edit_field",
            id=record.get("ID", "?"),
            desc=record.get("DESCRICAO", "-"),
            fields=_field_menu(lang),
        )
    )


def _prompt_for_field(lang: str, field_key: str, ctx) -> str:
    prompts = {
        "DESCRICAO": "ask_desc",
        "PROXIMA_DATA": "ask_date",
        "ORGAO": "ask_orgao",
        "FREQUENCIA": "ask_freq",
        "CRITICO": "ask_critico",
    }
    if field_key == "ALERTA_DIAS":
        return tr(lang, "ask_alerta", days=_nearest_days(ctx))
    return tr(lang, prompts[field_key])


def _apply_edit(ctx, lang, store, key, conv, text, reply) -> Response:
    field_key = conv.data.get("edit_field", "")
    deadline_id = conv.data.get("edit_id", "")

    if field_key == "DESCRICAO":
        if not text:
            return reply(tr(lang, "required_desc"))
        value = text
    elif field_key == "PROXIMA_DATA":
        parsed = parse_date(text)
        if parsed is None:
            return reply(tr(lang, "invalid_date"))
        value = format_date(parsed)
    elif field_key in ("ORGAO", "FREQUENCIA"):
        value = "" if text in _SKIP else text
    elif field_key == "CRITICO":
        value = "SIM" if text.lower() in _YES_WORDS else "NAO"
    else:  # ALERTA_DIAS
        value = str(to_int(text, _nearest_days(ctx)))

    store.end(key)
    try:
        updated = update_deadline(_file_path(ctx), deadline_id, {field_key: value})
    except OSError as exc:
        log.error("deadlines.update_failed", error=str(exc))
        return reply(tr(lang, "error"))
    if updated is None:
        return reply(tr(lang, "not_found"))
    log.info("deadlines.updated", id=deadline_id, field=field_key)
    return reply(tr(lang, "updated", id=deadline_id, summary=_summary(lang, updated)))
