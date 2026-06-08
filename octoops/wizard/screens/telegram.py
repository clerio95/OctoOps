from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Button, Input, Label, Static

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
    step_title = "Telegram (control plane)"

    def content(self) -> ComposeResult:
        yield Label("Bot token  (get one from @BotFather → /newbot)")
        yield Input(
            value=self.state.bot_token,
            password=True,
            id="bot_token",
            placeholder="123456:ABC-...",
        )
        yield Button("Verify token & auto-detect chat ID", id="pair")
        yield Static("", id="pair_status", classes="preview")
        yield Label("Admin chat ID (receives startup / error notices)")
        yield Input(
            value=self.state.admin_chat_id, id="admin_chat_id", placeholder="123456789"
        )

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
        if err := validate_bot_token(token):
            self._status(f"⚠ Bot token: {err}")
            return

        self._status("Checking token with Telegram…")
        api = TelegramApi(token.strip())
        try:
            try:
                identity = await verify_token(api)
            except VerifyNetworkError as exc:
                self._status(
                    f"✗ Could not reach Telegram — check your internet connection.\n({exc})"
                )
                return
            if identity is None:
                self._status("✗ Token rejected by Telegram — re-check it with @BotFather.")
                return

            nonce = new_nonce()
            link = make_start_link(identity.username, nonce)
            try:
                await api.delete_webhook()  # otherwise getUpdates 409s
            except Exception:  # noqa: BLE001 - best-effort; a lingering webhook
                # just surfaces later as the 409 that wait_for_start already handles.
                pass
            self._status(
                f"✓ Connected to @{identity.username}\n\n"
                f"Now open this link and press Start:\n{link}\n\n"
                "Waiting for you to press Start…"
            )

            try:
                result = await wait_for_start(api, nonce, timeout=_START_TIMEOUT)
            except BotAlreadyRunningError:
                self._status(
                    "✗ This bot looks like it's already running elsewhere, so Telegram "
                    "won't let setup read its messages. Stop that instance, or just type "
                    "your chat ID below."
                )
                return

            if result is None:
                self._status(
                    "Timed out waiting for Start. Press Start and click the button "
                    "again, or just type your chat ID below."
                )
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
                extra = f" Added you (user {result.user_id}) as an admin."
            self._status(
                f"✓ Got your chat ID ({result.chat_id}).{extra} You're set — press Next."
            )
        finally:
            await api.close()

    # --- save (manual entry remains a valid path) -----------------------------

    def save(self) -> str | None:
        token = self.query_one("#bot_token", Input).value
        chat = self.query_one("#admin_chat_id", Input).value
        if err := validate_bot_token(token):
            return f"Bot token: {err}"
        if err := validate_chat_id(chat):
            return f"Admin chat ID: {err}"
        self.state.bot_token = token.strip()
        self.state.admin_chat_id = chat.strip()
        return None
