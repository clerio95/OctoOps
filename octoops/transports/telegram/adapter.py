"""TelegramTransport — the control plane.

Receives commands via long polling, parses them into Requests, dispatches through
the Router, and routes the Response back (Telegram always; WhatsApp on mirror).
Uses python-telegram-bot's manual lifecycle so it embeds in our own event loop
rather than taking over signal handling.

Resilience: startup is supervised. A transient failure (network blip, temporary
5xx) is retried with exponential backoff; an InvalidToken is fatal (no hot loop).
The Application is always shut down before a retry, so nothing leaks.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telegram import Update
from telegram.error import BadRequest, InvalidToken
from telegram.ext import Application, MessageHandler, filters

from octoops.core.errors import TransportError
from octoops.core.logging import get_logger
from octoops.core.response_router import route_response
from octoops.shared.models import Request, Response, TransportSource
from octoops.transports import Transport

from .formatter import format_response

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from octoops.core.registry import Registry
    from octoops.core.router import Router

_log = get_logger("octoops.transports.telegram")

_MAX_BACKOFF = 60


def parse_command(text: str) -> tuple[str, list[str]]:
    """Split a message into (command, args). Strips a leading '/' and '@botname'."""
    parts = text.strip().split()
    if not parts:
        return "", []
    command = parts[0].lstrip("/").split("@", 1)[0].lower()
    return command, parts[1:]


class TelegramTransport(Transport):
    def __init__(self, token: str, admin_chat_id: str | None = None) -> None:
        self._token = token
        self._admin_chat_id = admin_chat_id
        self._app: Application | None = None
        self._router: "Router | None" = None
        self._registry: "Registry | None" = None

    @property
    def name(self) -> str:
        return "telegram"

    async def run(self, router: "Router", registry: "Registry") -> None:
        self._router = router
        self._registry = registry
        backoff = 1
        try:
            while True:
                try:
                    await self._start_app()
                except InvalidToken:
                    # Configuration error — retrying cannot help.
                    _log.error("telegram.invalid_token_fatal")
                    return
                except Exception as exc:  # noqa: BLE001 - transient startup failure
                    _log.error(
                        "telegram.start_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                        retry_in=backoff,
                    )
                    await self._shutdown()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)
                    continue

                backoff = 1
                await self._notify_admin("🐙 OctoOps started.")
                # Block until cancelled (shutdown). Polling runs in the background.
                await asyncio.Event().wait()
        finally:
            await self._shutdown()

    async def _start_app(self) -> None:
        self._app = Application.builder().token(self._token).build()
        # Only fresh messages: excludes edited messages, channel posts, and
        # business messages, which would otherwise re-fire commands.
        self._app.add_handler(
            MessageHandler(filters.TEXT & filters.UpdateType.MESSAGE, self._on_message)
        )
        await self._app.initialize()
        # Learn our own @username so the access module can build invite links.
        if self._registry is not None and self._app.bot.username:
            self._registry.bot_username = self._app.bot.username
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        _log.info("telegram.started")

    async def _on_message(
        self, update: Update, context: "ContextTypes.DEFAULT_TYPE"
    ) -> None:
        message = update.effective_message
        if message is None or not message.text:
            return
        if update.effective_user is None or update.effective_chat is None:
            return

        user_id = str(update.effective_user.id)
        assert self._router is not None and self._registry is not None

        command, args = parse_command(message.text)
        chat_id = str(update.effective_chat.id)

        # Transport-level allowlist gate: users in no role list are silently
        # ignored so the bot does not reveal itself or amplify spam. The one
        # exception is redeeming a valid one-time invite (/start <nonce>).
        if self._registry.permissions.role_for(user_id) is None:
            await self._maybe_redeem_invite(command, args, user_id, chat_id)
            return

        if not command:
            return

        request = Request(
            command=command,
            args=args,
            raw_text=message.text,
            user_id=user_id,
            chat_id=chat_id,
            source=TransportSource.Telegram,
        )
        response = await self._router.dispatch(request)
        if response is not None:
            await route_response(response, self._registry)

    async def _maybe_redeem_invite(
        self, command: str, args: list[str], user_id: str, chat_id: str
    ) -> None:
        """An unknown user's only allowed action: redeem a valid invite nonce.

        A valid /start <nonce> grants the invite's role and welcomes them; anything
        else (no nonce, wrong/expired nonce, any other command) is silent — the bot
        stays invisible to anyone without a live invite.
        """
        assert self._registry is not None
        invites = self._registry.invites
        if invites is None or command != "start" or not args:
            _log.info("telegram.ignored_unknown_user", user=user_id)
            return
        invite = invites.redeem(args[0])
        if invite is None:
            _log.info("telegram.ignored_unknown_user", user=user_id)
            return
        self._registry.permissions.grant(user_id, invite.role)
        _log.info("access.invite_redeemed", user=user_id, role=invite.role.name)
        await route_response(
            Response(
                text=f"✓ You now have {invite.role.name} access. Try /status.",
                chat_id=chat_id,
            ),
            self._registry,
        )

    async def send(self, response: Response) -> None:
        if self._app is None:
            raise TransportError("telegram transport not started")
        reply_to = (
            int(response.reply_to)
            if response.reply_to and str(response.reply_to).isdigit()
            else None
        )
        kwargs = format_response(response)
        try:
            await self._app.bot.send_message(
                chat_id=response.chat_id, reply_to_message_id=reply_to, **kwargs
            )
        except BadRequest:
            # Markdown parsing failed — resend as plain text.
            await self._app.bot.send_message(
                chat_id=response.chat_id,
                text=response.text,
                reply_to_message_id=reply_to,
            )

    async def _notify_admin(self, text: str) -> None:
        if not self._admin_chat_id or self._app is None:
            return
        try:
            await self._app.bot.send_message(chat_id=self._admin_chat_id, text=text)
        except Exception as exc:  # noqa: BLE001 - best effort
            _log.warning("telegram.admin_notify_failed", error=str(exc))

    async def _shutdown(self) -> None:
        if self._app is None:
            return
        try:
            if self._app.updater is not None and self._app.updater.running:
                await self._app.updater.stop()
            if self._app.running:
                await self._app.stop()
            await self._app.shutdown()
        except Exception as exc:  # noqa: BLE001
            _log.warning("telegram.shutdown_error", error=str(exc))
        finally:
            self._app = None
        _log.info("telegram.stopped")
