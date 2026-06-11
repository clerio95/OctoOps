"""Textual wizard smoke + happy-path flow via the test pilot.

Drives the real screens (compose, validation, navigation) headlessly. Buttons
are pressed on the *top* screen object to avoid ambiguity with stacked screens.
"""

import pytest
from textual import events
from textual.widgets import Button, Input, RadioButton, RadioSet, Select, Static, Switch

from octoops.core.plugin_loader import DiscoveredModule, Manifest
from octoops.core.contracts import ModuleRegistration
from octoops.wizard import app as app_mod
from octoops.wizard.app import WizardApp
from octoops.wizard.screens.core_settings import _TZ_CUSTOM
from octoops.wizard.state import WizardState


def _discovered() -> list[DiscoveredModule]:
    return [
        DiscoveredModule(
            manifest=Manifest(name="status", version="1.0.0", description="status"),
            registration=ModuleRegistration(name="status"),  # no config fields
        )
    ]


def _press(app, button_id: str) -> None:
    app.screen.query_one(f"#{button_id}", Button).press()


async def _skip_language(app, pilot) -> None:
    """Advance past the new first screen (language picker) to welcome."""
    assert app.screen.STEP_ID == "language"
    _press(app, "next")  # language -> welcome (English default)
    await pilot.pause()


@pytest.mark.asyncio
async def test_language_then_welcome_mount():
    app = WizardApp(discovered=_discovered(), config_exists=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        # The language picker is the first screen and has no Back button.
        assert app.screen.STEP_ID == "language"
        assert len(app.screen.query("#back")) == 0
        await _skip_language(app, pilot)
        # Welcome follows; it warns about overwrite when a config exists, and now
        # has a Back button (returning to the language picker).
        assert app.screen.STEP_ID == "welcome"
        assert "Welcome" in str(app.screen.query_one(".step-title").render())
        assert len(app.screen.query("#back")) == 1


@pytest.mark.asyncio
async def test_cancel_returns_none():
    app = WizardApp(discovered=_discovered())
    async with app.run_test() as pilot:
        await pilot.pause()
        _press(app, "cancel")
        await pilot.pause()
    assert app.return_value is None


@pytest.mark.asyncio
async def test_full_happy_path_writes_state():
    app = WizardApp(discovered=_discovered(), config_exists=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _skip_language(app, pilot)
        _press(app, "next")  # welcome -> telegram
        await pilot.pause()

        app.screen.query_one("#bot_token", Input).value = "123456:ABC-def"
        app.screen.query_one("#admin_chat_id", Input).value = "999"
        _press(app, "next")  # telegram -> whatsapp
        await pilot.pause()

        _press(app, "next")  # whatsapp (defaults ok) -> core
        await pilot.pause()

        # core defaults: detected local timezone, viewer, log path. At least one
        # user must be authorized, so add an admin before advancing.
        app.screen.query_one("#admins", Input).value = "999"
        _press(app, "next")  # core -> modules
        await pilot.pause()

        _press(app, "next")  # modules (status preselected); no fields/task -> summary
        await pilot.pause()

        # On summary now; Finish exits with the state.
        assert "Review" in str(app.screen.query_one(".step-title").render())
        _press(app, "next")
        await pilot.pause()

    result = app.return_value
    assert isinstance(result, WizardState)
    assert result.bot_token == "123456:ABC-def"
    assert result.admin_chat_id == "999"
    assert result.admin_user_ids == ["999"]
    assert result.enabled_modules == ["status"]


async def _advance_to_core(app, pilot) -> None:
    await _skip_language(app, pilot)
    _press(app, "next")  # welcome -> telegram
    await pilot.pause()
    app.screen.query_one("#bot_token", Input).value = "123456:ABC-def"
    app.screen.query_one("#admin_chat_id", Input).value = "999"
    _press(app, "next")  # telegram -> whatsapp
    await pilot.pause()
    _press(app, "next")  # whatsapp -> core
    await pilot.pause()
    assert app.screen.STEP_ID == "core"


@pytest.mark.asyncio
async def test_core_timezone_dropdown_picks_curated_zone():
    # A zone in the curated list is preselected in the Select (not the custom
    # sentinel), and saving carries it through to state.
    state = WizardState(timezone="America/Sao_Paulo", admin_user_ids=["999"])
    app = WizardApp(discovered=_discovered(), state=state)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _advance_to_core(app, pilot)
        assert app.screen.query_one("#timezone", Select).value == "America/Sao_Paulo"
        _press(app, "next")  # core -> modules
        await pilot.pause()
    assert app.state.timezone == "America/Sao_Paulo"


@pytest.mark.asyncio
async def test_core_timezone_custom_entry():
    # A zone outside the curated list falls back to the custom field, and a
    # custom IANA zone typed there is validated and saved.
    state = WizardState(timezone="Pacific/Auckland", admin_user_ids=["999"])
    app = WizardApp(discovered=_discovered(), state=state)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _advance_to_core(app, pilot)
        assert app.screen.query_one("#timezone", Select).value == _TZ_CUSTOM
        assert app.screen.query_one("#timezone_custom", Input).value == "Pacific/Auckland"
        app.screen.query_one("#timezone_custom", Input).value = "Asia/Tokyo"
        _press(app, "next")  # core -> modules
        await pilot.pause()
    assert app.state.timezone == "Asia/Tokyo"


@pytest.mark.asyncio
async def test_whatsapp_screen_saves_admin_chat_ids():
    # Enabling WhatsApp and entering admin numbers carries them into state, so the
    # startup notification has recipients (regression for the empty-list gap).
    app = WizardApp(discovered=_discovered(), config_exists=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _skip_language(app, pilot)
        _press(app, "next")  # welcome -> telegram
        await pilot.pause()
        app.screen.query_one("#bot_token", Input).value = "123456:ABC-def"
        app.screen.query_one("#admin_chat_id", Input).value = "999"
        _press(app, "next")  # telegram -> whatsapp
        await pilot.pause()
        assert app.screen.STEP_ID == "whatsapp"
        app.screen.query_one("#use_whatsapp", Switch).value = True
        await pilot.pause()
        app.screen.query_one("#wa_admins", Input).value = "5511999998888 5511888887777"
        _press(app, "next")  # whatsapp -> core
        await pilot.pause()
    assert app.state.use_whatsapp is True
    assert app.state.whatsapp_admin_chat_ids == ["5511999998888", "5511888887777"]


async def _telegram_token_input(app, pilot):
    await pilot.pause()
    await _skip_language(app, pilot)
    _press(app, "next")  # welcome -> telegram (has a bot_token Input)
    await pilot.pause()
    token = app.screen.query_one("#bot_token", Input)
    token.focus()
    await pilot.pause()
    return token


@pytest.mark.asyncio
async def test_ctrl_v_inserts_clipboard_text_into_focused_input(monkeypatch):
    # Regression guard: the paste fallback must call the *public* insert API and
    # actually drop the clipboard text into the focused Input. The clipboard
    # reader is stubbed so this runs on any platform. The insert is deferred by
    # PASTE_FALLBACK_DELAY (native-paste dedup), so the test waits it out.
    monkeypatch.setattr(app_mod, "_clipboard_paste", lambda: "PASTED")
    app = WizardApp(discovered=_discovered(), config_exists=False)
    app.PASTE_FALLBACK_DELAY = 0.05
    async with app.run_test() as pilot:
        token = await _telegram_token_input(app, pilot)
        app.on_key(events.Key("ctrl+v", None))
        await pilot.pause(0.3)
        assert token.value == "PASTED"


@pytest.mark.asyncio
async def test_ctrl_v_key_does_not_double_paste(monkeypatch):
    # One real ctrl+v key dispatch must insert the clipboard text exactly once:
    # Input has its own ctrl+v binding (pastes Textual's internal clipboard)
    # which previously ran IN ADDITION to the app-level fallback.
    monkeypatch.setattr(app_mod, "_clipboard_paste", lambda: "PASTED")
    app = WizardApp(discovered=_discovered(), config_exists=False)
    app.PASTE_FALLBACK_DELAY = 0.05
    async with app.run_test() as pilot:
        token = await _telegram_token_input(app, pilot)
        app._clipboard = "INTERNAL"  # would be appended by Input's own binding
        await pilot.press("ctrl+v")
        await pilot.pause(0.3)
        assert token.value == "PASTED"


@pytest.mark.asyncio
async def test_ctrl_v_skips_fallback_when_terminal_pasted_natively(monkeypatch):
    # Terminals that paste natively AND forward the raw ctrl+v key were the
    # doubling bug from live testing. Whether the native paste lands before the
    # key or during the fallback window, only one copy may remain.
    monkeypatch.setattr(app_mod, "_clipboard_paste", lambda: "PASTED")
    app = WizardApp(discovered=_discovered(), config_exists=False)
    app.PASTE_FALLBACK_DELAY = 0.05
    async with app.run_test() as pilot:
        token = await _telegram_token_input(app, pilot)
        # Native paste arrived first (bracketed paste -> Input inserted it).
        token.insert_text_at_cursor("PASTED")
        app.on_key(events.Key("ctrl+v", None))
        await pilot.pause(0.3)
        assert token.value == "PASTED"
        # Native paste arrives DURING the fallback window (conhost keystroke
        # injection): the fallback must notice the change and stand down.
        token.value = ""
        app.on_key(events.Key("ctrl+v", None))
        token.insert_text_at_cursor("PASTED")
        await pilot.pause(0.3)
        assert token.value == "PASTED"


@pytest.mark.asyncio
async def test_show_token_checkbox_toggles_password_masking():
    from textual.widgets import Checkbox

    app = WizardApp(discovered=_discovered(), config_exists=False)
    async with app.run_test() as pilot:
        token = await _telegram_token_input(app, pilot)
        assert token.password is True  # hidden by default
        app.screen.query_one("#show_token", Checkbox).value = True
        await pilot.pause()
        assert token.password is False
        app.screen.query_one("#show_token", Checkbox).value = False
        await pilot.pause()
        assert token.password is True


@pytest.mark.asyncio
async def test_userid_hint_shown_on_telegram_and_core_screens():
    # Live-testing feedback: users didn't know how to find a Telegram user ID.
    # Both ID-entry screens must mention the @userinfobot lookup path.
    from textual.widgets import Static, Switch

    app = WizardApp(discovered=_discovered(), config_exists=False)
    async with app.run_test() as pilot:
        await _telegram_token_input(app, pilot)
        hint = app.screen.query_one("#userid_hint", Static)
        assert "@userinfobot" in str(hint.render())
        app.screen.query_one("#bot_token", Input).value = "123456:ABC-def"
        app.screen.query_one("#admin_chat_id", Input).value = "999"
        _press(app, "next")  # telegram -> whatsapp
        await pilot.pause()
        _press(app, "next")  # whatsapp -> core
        await pilot.pause()
        assert app.screen.STEP_ID == "core"
        hint = app.screen.query_one("#userid_hint", Static)
        assert "@userinfobot" in str(hint.render())


@pytest.mark.asyncio
async def test_core_step_blocks_when_no_user_authorized():
    # With every id list empty, the core step must refuse to advance (otherwise
    # the bot would silently ignore everyone) and show an error instead.
    app = WizardApp(discovered=_discovered(), config_exists=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _skip_language(app, pilot)
        _press(app, "next")  # welcome -> telegram
        await pilot.pause()
        app.screen.query_one("#bot_token", Input).value = "123456:ABC-def"
        app.screen.query_one("#admin_chat_id", Input).value = "999"
        _press(app, "next")  # telegram -> whatsapp
        await pilot.pause()
        _press(app, "next")  # whatsapp -> core
        await pilot.pause()

        # Leave all id fields empty and try to advance.
        assert app.screen.STEP_ID == "core"
        _press(app, "next")
        await pilot.pause()
        # Still on core, with the authorization error shown.
        assert app.screen.STEP_ID == "core"
        assert "Authorize at least one user" in str(
            app.screen.query_one("#error").render()
        )


@pytest.mark.asyncio
async def test_botfather_link_shown_on_telegram_screen():
    # The Telegram step must point users at BotFather to create their token.
    app = WizardApp(discovered=_discovered(), config_exists=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _skip_language(app, pilot)
        _press(app, "next")  # welcome -> telegram
        await pilot.pause()
        assert app.screen.STEP_ID == "telegram"
        texts = " ".join(str(s.render()) for s in app.screen.query(Static))
        assert "telegram.me/BotFather" in texts


@pytest.mark.asyncio
async def test_pt_br_language_localizes_following_screens():
    # Picking Português-BR on the first screen makes every later screen (and the
    # nav buttons) render in Portuguese.
    app = WizardApp(discovered=_discovered(), config_exists=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.STEP_ID == "language"
        # Select the second option (Português-BR); RadioSet enforces exclusivity.
        radio = app.screen.query_one("#language", RadioSet)
        list(app.screen.query(RadioButton))[1].value = True
        await pilot.pause()
        assert radio.pressed_index == 1
        _press(app, "next")  # language -> welcome (now in pt-BR)
        await pilot.pause()

        assert app.language == "pt-BR"
        assert app.screen.STEP_ID == "welcome"
        assert "Bem-vindo" in str(app.screen.query_one(".step-title").render())
        # The welcome "Begin" button is localized.
        assert "Começar" in str(app.screen.query_one("#next", Button).label)

        _press(app, "next")  # welcome -> telegram
        await pilot.pause()
        assert "plano de controle" in str(app.screen.query_one(".step-title").render())
