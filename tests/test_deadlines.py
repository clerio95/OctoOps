"""deadlines module: JSON storage, the interactive flow, and localization."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from octoops.core.config import (
    AppConfig,
    CoreConfig,
    TelegramConfig,
    TransportConfig,
)
from octoops.core.event_bus import EventBus
from octoops.core.permissions import Permissions
from octoops.core.registry import ModuleConfig, ModuleContext, Registry
from octoops.core.scheduler import Scheduler
from octoops.modules.deadlines import handle, load
from octoops.modules.deadlines import storage
from octoops.shared.models import Request, Role, TransportSource

TZ = "America/Sao_Paulo"


# --- storage -----------------------------------------------------------------


def test_parse_date_accepts_both_formats():
    assert storage.parse_date("25/12/2026") == date(2026, 12, 25)
    assert storage.parse_date("2026-12-25") == date(2026, 12, 25)
    assert storage.parse_date("nonsense") is None


def test_format_date_is_ddmmyyyy():
    assert storage.format_date(date(2026, 1, 5)) == "05/01/2026"


def test_add_and_load_roundtrip(tmp_path):
    path = tmp_path / "d.json"
    saved = storage.add_deadline(path, {"descricao": "IPVA", "proxima_data": "25/12/2026"})
    assert saved["ID"]  # an ID was assigned
    rows = storage.load_deadlines(path)
    assert len(rows) == 1
    # Keys are upper-cased on read; the ID survives.
    assert rows[0]["DESCRICAO"] == "IPVA"
    assert rows[0]["PROXIMA_DATA"] == "25/12/2026"
    assert rows[0]["ID"] == saved["ID"]


def test_ids_are_unique(tmp_path):
    path = tmp_path / "d.json"
    a = storage.add_deadline(path, {"descricao": "a", "proxima_data": "01/01/2030"})
    b = storage.add_deadline(path, {"descricao": "b", "proxima_data": "02/01/2030"})
    assert a["ID"] != b["ID"]


def test_load_missing_file_is_empty(tmp_path):
    assert storage.load_deadlines(tmp_path / "nope.json") == []


def test_load_corrupt_file_is_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    assert storage.load_deadlines(path) == []


def test_corrupt_file_is_quarantined_so_adds_cannot_destroy_it(tmp_path):
    """A hand-edit gone wrong (stray comma, BOM) must never be silently erased
    by the next add: the corrupt original is moved aside first."""
    path = tmp_path / "d.json"
    path.write_text('[{"DESCRICAO": "IPVA"', encoding="utf-8")  # truncated JSON

    assert storage.load_deadlines(path) == []
    quarantined = list(tmp_path.glob("d.json.corrupt-*"))
    assert len(quarantined) == 1
    assert "IPVA" in quarantined[0].read_text(encoding="utf-8")

    saved = storage.add_deadline(path, {"descricao": "new", "proxima_data": "01/01/2030"})
    assert saved["ID"]
    assert quarantined[0].exists()  # the original data survives the new write
    assert len(storage.load_deadlines(path)) == 1


def test_wrong_shape_file_is_quarantined(tmp_path):
    path = tmp_path / "d.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")
    assert storage.load_deadlines(path) == []
    assert len(list(tmp_path.glob("d.json.corrupt-*"))) == 1


def test_upcoming_filters_window_and_sorts():
    today = date(2026, 1, 10)
    rows = [
        {"DESCRICAO": "soon", "PROXIMA_DATA": "12/01/2026"},     # +2
        {"DESCRICAO": "later", "PROXIMA_DATA": "20/01/2026"},    # +10
        {"DESCRICAO": "far", "PROXIMA_DATA": "01/03/2026"},      # out of window
        {"DESCRICAO": "past", "PROXIMA_DATA": "01/01/2026"},     # already overdue
    ]
    out = storage.upcoming(rows, within_days=15, today=today)
    assert [r["DESCRICAO"] for r in out] == ["soon", "later"]
    assert out[0]["_remaining"] == 2


def test_all_sorted_puts_undated_last():
    today = date(2026, 1, 10)
    rows = [
        {"DESCRICAO": "b", "PROXIMA_DATA": "20/01/2026"},
        {"DESCRICAO": "nodate"},
        {"DESCRICAO": "a", "PROXIMA_DATA": "12/01/2026"},
    ]
    out = storage.all_sorted(rows, today)
    assert [r["DESCRICAO"] for r in out] == ["a", "b", "nodate"]


# --- the interactive flow ----------------------------------------------------


def _ctx(tmp_path, language="en", nearest_days="15") -> ModuleContext:
    path = tmp_path / "deadlines.json"
    cfg = AppConfig(
        telegram=TelegramConfig(bot_token="t", admin_chat_id="1"),
        transport=TransportConfig(),
        core=CoreConfig(timezone=TZ, language=language),
    )
    registry = Registry(
        config=cfg,
        event_bus=EventBus(),
        scheduler=Scheduler(timezone=TZ),
        permissions=Permissions(
            allowed_user_ids=[], operator_user_ids=[], admin_user_ids=[]
        ),
        start_time=datetime.now(ZoneInfo(TZ)),
    )
    module_cfg = ModuleConfig({"file_path": str(path), "nearest_days": nearest_days})
    return ModuleContext(
        name="deadlines",
        config=module_cfg,
        registry=registry,
        event_bus=registry.event_bus,
        scheduler=registry.scheduler,
    )


def _req(ctx: ModuleContext, command: str, text: str) -> Request:
    return Request(
        command=command,
        args=[text] if text else [],
        raw_text=text,
        user_id="u1",
        chat_id="c1",
        source=TransportSource.Telegram,
    )


async def _send(ctx, command, text) -> str:
    resp = await handle(_req(ctx, command, text), ctx)
    return resp.text


async def test_fresh_invocation_shows_menu(tmp_path):
    ctx = _ctx(tmp_path)
    text = await _send(ctx, "deadlines", "")
    assert "1️⃣" in text and "2️⃣" in text and "3️⃣" in text
    assert "15 days" in text  # the nearest window


async def test_menu_nearest_when_empty(tmp_path):
    ctx = _ctx(tmp_path)
    await _send(ctx, "deadlines", "")  # open menu
    text = await _send(ctx, "deadlines", "1")
    assert "No deadlines in the next 15 days" in text


async def test_full_add_flow_persists_record(tmp_path):
    ctx = _ctx(tmp_path)
    due = date.today() + timedelta(days=3)
    due_str = storage.format_date(due)

    assert "1️⃣" in await _send(ctx, "deadlines", "")          # menu
    assert "description" in (await _send(ctx, "deadlines", "3")).lower()  # ask desc
    assert "date" in (await _send(ctx, "deadlines", "Car tax")).lower()   # ask date
    # bad date -> reprompt, flow stays on the date step
    assert "couldn't read" in (await _send(ctx, "deadlines", "32/13/2026")).lower()
    assert "body" in (await _send(ctx, "deadlines", due_str)).lower()     # ask orgao
    await _send(ctx, "deadlines", "DETRAN")                                # orgao -> freq
    await _send(ctx, "deadlines", "Yearly")                               # freq -> critico
    await _send(ctx, "deadlines", "yes")                                  # critico -> alerta
    saved = await _send(ctx, "deadlines", "")                             # default alerta -> save
    assert "Saved" in saved

    rows = storage.load_deadlines(ctx.config.get("file_path"))
    assert len(rows) == 1
    rec = rows[0]
    assert rec["DESCRICAO"] == "Car tax"
    assert rec["PROXIMA_DATA"] == due_str
    assert rec["ORGAO"] == "DETRAN"
    assert rec["FREQUENCIA"] == "Yearly"
    assert rec["CRITICO"] == "SIM"
    assert rec["ALERTA_DIAS"] == "15"  # defaulted to the nearest window
    assert rec["ID"]


async def test_nearest_lists_the_added_deadline(tmp_path):
    ctx = _ctx(tmp_path)
    due = storage.format_date(date.today() + timedelta(days=5))
    storage.add_deadline(ctx.config.get("file_path"), {"DESCRICAO": "Insurance", "PROXIMA_DATA": due})
    await _send(ctx, "deadlines", "")  # menu
    text = await _send(ctx, "deadlines", "1")
    assert "Insurance" in text
    assert "Nearest deadlines (1)" in text


async def test_skip_optional_fields_with_dash(tmp_path):
    ctx = _ctx(tmp_path)
    due = storage.format_date(date.today() + timedelta(days=2))
    await _send(ctx, "deadlines", "")
    await _send(ctx, "deadlines", "3")
    await _send(ctx, "deadlines", "Thing")
    await _send(ctx, "deadlines", due)
    await _send(ctx, "deadlines", "-")  # skip orgao
    await _send(ctx, "deadlines", "-")  # skip freq
    await _send(ctx, "deadlines", "no")  # not critical
    await _send(ctx, "deadlines", "30")  # explicit alert days
    rec = storage.load_deadlines(ctx.config.get("file_path"))[0]
    assert "ORGAO" not in rec
    assert "FREQUENCIA" not in rec
    assert rec["CRITICO"] == "NAO"
    assert rec["ALERTA_DIAS"] == "30"


async def test_cancel_aborts_flow(tmp_path):
    ctx = _ctx(tmp_path)
    await _send(ctx, "deadlines", "")
    await _send(ctx, "deadlines", "3")  # into the add flow
    text = await _send(ctx, "deadlines", "cancel")
    assert "cancelled" in text.lower()
    # Conversation is gone -> a new message starts at the menu again.
    assert "1️⃣" in await _send(ctx, "deadlines", "anything")


async def test_invalid_menu_choice_reprompts(tmp_path):
    ctx = _ctx(tmp_path)
    await _send(ctx, "deadlines", "")
    text = await _send(ctx, "deadlines", "9")
    assert "1, 2, 3 or 4" in text


async def test_whatsapp_source_keeps_its_own_conversation(tmp_path):
    # Telegram and WhatsApp users must not share a flow (keyed per transport).
    ctx = _ctx(tmp_path)
    tg = Request(command="deadlines", args=[], raw_text="", user_id="u1",
                 chat_id="c1", source=TransportSource.Telegram)
    wa = Request(command="vencimentos", args=["vencimentos"], raw_text="vencimentos",
                 user_id="u1", chat_id="c1", source=TransportSource.WhatsApp)
    await handle(tg, ctx)  # opens a Telegram menu
    # WhatsApp user (same id) is still fresh -> the keyword opens its own menu.
    assert "1️⃣" in (await handle(wa, ctx)).text


# --- WhatsApp keyword gate ---------------------------------------------------


def _wa(ctx, text) -> Request:
    return Request(
        command="vencimentos",
        args=[text] if text else [],
        raw_text=text,
        user_id="wa1",
        chat_id="wa1",
        source=TransportSource.WhatsApp,
    )


async def test_whatsapp_non_keyword_is_silent(tmp_path):
    ctx = _ctx(tmp_path)
    resp = await handle(_wa(ctx, "oi"), ctx)
    assert resp is None  # None -> no reply is sent at all


async def test_whatsapp_keyword_opens_menu(tmp_path):
    ctx = _ctx(tmp_path)
    for kw in ("vencimentos", "/vencimentos", "deadlines", "Vencimentos hoje"):
        ctx.registry.conversations.end(("whatsapp", "wa1"))  # reset between cases
        assert "1️⃣" in (await handle(_wa(ctx, kw), ctx)).text


async def test_whatsapp_active_conversation_consumes_any_message(tmp_path):
    ctx = _ctx(tmp_path)
    await handle(_wa(ctx, "vencimentos"), ctx)  # menu open
    # Now "oi" is taken as input (menu choice), not gated/silenced.
    text = (await handle(_wa(ctx, "oi"), ctx)).text
    assert "1, 2, 3 or 4" in text  # invalid menu choice -> reprompt (i.e. consumed)


# --- conversation timeout feedback ---------------------------------------------


def _fake_clock_store(ctx, ttl=10.0):
    from octoops.core.conversations import ConversationStore

    now = [1000.0]
    ctx.registry.conversations = ConversationStore(
        ttl_seconds=ttl, clock=lambda: now[0]
    )
    return now


async def test_whatsapp_expired_flow_notice_once_then_silent(tmp_path):
    ctx = _ctx(tmp_path)
    now = _fake_clock_store(ctx)
    await handle(_wa(ctx, "vencimentos"), ctx)  # menu open
    now[0] += 11.0  # flow times out

    # The stale reply gets one "conversation expired" notice...
    resp = await handle(_wa(ctx, "3"), ctx)
    assert resp is not None and "timed out" in resp.text
    # ...and after that, non-keyword messages are silent again.
    assert await handle(_wa(ctx, "3"), ctx) is None


async def test_whatsapp_keyword_after_expiry_just_reopens_menu(tmp_path):
    ctx = _ctx(tmp_path)
    now = _fake_clock_store(ctx)
    await handle(_wa(ctx, "vencimentos"), ctx)
    now[0] += 11.0
    # The trigger keyword starts fresh — no confusing timeout notice in the way.
    assert "1️⃣" in (await handle(_wa(ctx, "vencimentos"), ctx)).text


async def test_telegram_expired_reply_gets_notice(tmp_path):
    # Simulates the adapter forwarding a stale plain reply (raw_text has no '/').
    ctx = _ctx(tmp_path)
    now = _fake_clock_store(ctx)
    await _send(ctx, "deadlines", "")  # opens the menu / the flow
    now[0] += 11.0
    resp = await handle(_req(ctx, "deadlines", "3"), ctx)
    assert "timed out" in resp.text


async def test_telegram_explicit_command_after_expiry_opens_menu(tmp_path):
    ctx = _ctx(tmp_path)
    now = _fake_clock_store(ctx)
    await _send(ctx, "deadlines", "")
    now[0] += 11.0
    req = Request(
        command="deadlines",
        args=[],
        raw_text="/deadlines",
        user_id="u1",
        chat_id="c1",
        source=TransportSource.Telegram,
    )
    assert "1️⃣" in (await handle(req, ctx)).text  # menu, not the timeout notice


# --- edit / delete -----------------------------------------------------------


def _seed(ctx, **fields) -> str:
    from octoops.modules.deadlines import storage
    base = {"DESCRICAO": "IPVA", "PROXIMA_DATA": "25/12/2026"}
    base.update(fields)
    return storage.add_deadline(ctx.config.get("file_path"), base)["ID"]


async def test_edit_changes_a_field(tmp_path):
    ctx = _ctx(tmp_path)
    did = _seed(ctx)
    await _send(ctx, "deadlines", "")          # menu
    listing = await _send(ctx, "deadlines", "4")  # edit -> pick list
    assert "1." in listing and "IPVA" in listing
    fields = await _send(ctx, "deadlines", "1")    # pick first deadline -> field menu
    assert "Description" in fields and "Delete this deadline" in fields
    await _send(ctx, "deadlines", "2")             # choose "Due date"
    done = await _send(ctx, "deadlines", "31/01/2027")  # new date
    assert "Updated" in done

    from octoops.modules.deadlines import storage
    rec = storage.find_deadline(storage.load_deadlines(ctx.config.get("file_path")), did)
    assert rec["PROXIMA_DATA"] == "31/01/2027"


async def test_edit_invalid_date_reprompts(tmp_path):
    ctx = _ctx(tmp_path)
    _seed(ctx)
    await _send(ctx, "deadlines", "")
    await _send(ctx, "deadlines", "4")
    await _send(ctx, "deadlines", "1")
    await _send(ctx, "deadlines", "2")             # due date
    bad = await _send(ctx, "deadlines", "99/99/9999")
    assert "couldn't read" in bad.lower()


async def test_edit_empty_when_none(tmp_path):
    ctx = _ctx(tmp_path)
    await _send(ctx, "deadlines", "")
    text = await _send(ctx, "deadlines", "4")
    assert "No deadlines to edit" in text


async def test_delete_with_confirmation(tmp_path):
    ctx = _ctx(tmp_path)
    did = _seed(ctx)
    await _send(ctx, "deadlines", "")
    await _send(ctx, "deadlines", "4")
    await _send(ctx, "deadlines", "1")             # pick
    confirm = await _send(ctx, "deadlines", "7")   # delete option
    assert "Delete" in confirm
    done = await _send(ctx, "deadlines", "yes")
    assert "Deleted" in done

    from octoops.modules.deadlines import storage
    assert storage.load_deadlines(ctx.config.get("file_path")) == []


async def test_delete_declined_keeps_record(tmp_path):
    ctx = _ctx(tmp_path)
    _seed(ctx)
    await _send(ctx, "deadlines", "")
    await _send(ctx, "deadlines", "4")
    await _send(ctx, "deadlines", "1")
    await _send(ctx, "deadlines", "7")
    done = await _send(ctx, "deadlines", "no")
    assert "nothing deleted" in done.lower()

    from octoops.modules.deadlines import storage
    assert len(storage.load_deadlines(ctx.config.get("file_path"))) == 1


# --- localization ------------------------------------------------------------


async def test_ptbr_menu_is_portuguese(tmp_path):
    ctx = _ctx(tmp_path, language="pt-BR")
    text = await _send(ctx, "vencimentos", "")
    assert "Vencimentos" in text
    assert "Próximos" in text


def test_ptbr_registers_both_commands_and_display(tmp_path):
    ctx = _ctx(tmp_path, language="pt-BR")
    reg = load(ctx)
    assert reg.name == "Vencimentos"  # display localizes
    assert {c.name for c in reg.commands} == {"vencimentos", "deadlines"}
    assert all(c.min_role is Role.Operator for c in reg.commands)


def test_en_display_is_deadlines(tmp_path):
    ctx = _ctx(tmp_path, language="en")
    reg = load(ctx)
    assert reg.name == "Deadlines"
    assert {c.name for c in reg.commands} == {"vencimentos", "deadlines"}
