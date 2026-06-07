"""Textual wizard smoke + happy-path flow via the test pilot.

Drives the real screens (compose, validation, navigation) headlessly. Buttons
are pressed on the *top* screen object to avoid ambiguity with stacked screens.
"""

import pytest
from textual.widgets import Button, Input

from octoops.core.plugin_loader import DiscoveredModule, Manifest
from octoops.core.contracts import ModuleRegistration
from octoops.wizard.app import WizardApp
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


@pytest.mark.asyncio
async def test_welcome_mounts():
    app = WizardApp(discovered=_discovered(), config_exists=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Welcome has no Back button, and warns about overwrite when config exists.
        assert len(app.screen.query("#back")) == 0
        assert "Welcome" in str(app.screen.query_one(".step-title").render())


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


@pytest.mark.asyncio
async def test_core_step_blocks_when_no_user_authorized():
    # With every id list empty, the core step must refuse to advance (otherwise
    # the bot would silently ignore everyone) and show an error instead.
    app = WizardApp(discovered=_discovered(), config_exists=False)
    async with app.run_test() as pilot:
        await pilot.pause()
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
