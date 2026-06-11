from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Button, Checkbox, Input, Label, Static

from octoops.wizard.screens.base import BaseStep
from octoops.wizard.state import validate_bot_token, validate_chat_id
from octoops.wizard.telegram_pairing import (
    BotAlreadyRunningError,
    TelegramApi,
    VerifyNetworkError,
    make_start_link,
    new_nonce,
    verify_token,
    wait_for_start,
)

_START_TIMEOUT = 180.0


class TelegramStep(BaseStep):
    STEP_ID = "telegram"
    title_key = "telegram.title"

    def content(self) -> ComposeResult:
        yield Static(self.tr("telegram.botfather_hint"), classes="warn")
        yield Label(self.tr("telegram.token_label"))
        yield Input(
            value=self.state.bot_token,
            password=True,
            id="bot_token",
            placeholder="123456:ABC-...",
        )
        yield Checkbox(self.tr("telegram.show_token"), value=False, id="show_token")
        yield Button(self.tr("telegram.verify_button"), id="pair")
        yield Static("", id="pair_status", classes="preview")
        yield Label(self.tr("telegram.admin_label"))
        yield Static(self.tr("telegram.userid_hint"), classes="warn", id="userid_hint")
        yield Input(
            value=self.state.admin_chat_id, id="admin_chat_id", placeholder="123456789"
        )

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "show_token":
            self.query_one("#bot_token", Input).password = not event.value

    # --- guided pairing -------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pair":
            event.stop()
            # exclusive: a second press cancels an in-flight wait and restarts.
            self.run_worker(self._pair(), exclusive=True, group="tg_pair")
        else:
            super().on_button_pressed(event)

    def _status(self, message: str) -> None:
        self.query_one("#pair_status", Static).update(message)

    async def _pair(self) -> None:
        token = self.query_one("#bot_token", Input).value
        if err := validate_bot_token(token, self.lang):
            self._status(self.tr("telegram.pair.token_warn", err=err))
            return

        self._status(self.tr("telegram.pair.checking"))
        api = TelegramApi(token.strip())
        try:
            try:
                identity = await verify_token(api)
            except VerifyNetworkError as exc:
                self._status(self.tr("telegram.pair.unreachable", exc=exc))
                return
            if identity is None:
                self._status(self.tr("telegram.pair.rejected"))
                return

            nonce = new_nonce()
            link = make_start_link(identity.username, nonce)
            try:
                await api.delete_webhook()  # otherwise getUpdates 409s
            except Exception:  # noqa: BLE001 - best-effort; a lingering webhook
                # just surfaces later as the 409 that wait_for_start already handles.
                pass
            self._status(
                self.tr(
                    "telegram.pair.connected",
                    username=identity.username,
                    link=link,
                )
            )

            try:
                result = await wait_for_start(api, nonce, timeout=_START_TIMEOUT)
            except BotAlreadyRunningError:
                self._status(self.tr("telegram.pair.already_running"))
                return

            if result is None:
                self._status(self.tr("telegram.pair.timeout"))
                return

            self.query_one("#admin_chat_id", Input).value = result.chat_id
            self.state.bot_token = token.strip()
            self.state.admin_chat_id = result.chat_id
            # The person who pressed Start is the operator running setup — add their
            # user id to the admin whitelist so they don't have to look it up later.
            # (Pre-fills the Core settings screen, which renders after this one.)
            extra = ""
            if result.user_id and result.user_id not in self.state.admin_user_ids:
                self.state.admin_user_ids.append(result.user_id)
                extra = self.tr("telegram.pair.added_admin", user_id=result.user_id)
            self._status(
                self.tr("telegram.pair.got_chat", chat_id=result.chat_id, extra=extra)
            )
        finally:
            await api.close()

    # --- save (manual entry remains a valid path) -----------------------------

    def save(self) -> str | None:
        token = self.query_one("#bot_token", Input).value
        chat = self.query_one("#admin_chat_id", Input).value
        if err := validate_bot_token(token, self.lang):
            return self.tr("telegram.err.token", err=err)
        if err := validate_chat_id(chat, self.lang):
            return self.tr("telegram.err.chat", err=err)
        self.state.bot_token = token.strip()
        self.state.admin_chat_id = chat.strip()
        return None
