from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Input, Label, Static, Switch

from octoops.wizard.screens.base import BaseStep
from octoops.wizard.state import (
    parse_id_list,
    validate_port,
    validate_required,
    validate_role,
)


class WhatsAppStep(BaseStep):
    STEP_ID = "whatsapp"
    title_key = "whatsapp.title"

    def content(self) -> ComposeResult:
        yield Static(self.tr("whatsapp.intro"))
        yield Label(self.tr("whatsapp.enable_label"))
        yield Switch(value=self.state.use_whatsapp, id="use_whatsapp")
        yield Label(self.tr("whatsapp.bridge_path"))
        yield Input(value=self.state.whatsapp_bridge_path, id="bridge_path")
        yield Label(self.tr("whatsapp.bridge_port"))
        yield Input(value=str(self.state.whatsapp_bridge_port), id="bridge_port", type="integer")
        yield Label(self.tr("whatsapp.callback_port"))
        yield Input(
            value=str(self.state.octoops_callback_port), id="callback_port", type="integer"
        )
        yield Label(self.tr("whatsapp.admins_label"))
        yield Input(
            value=" ".join(self.state.whatsapp_admin_chat_ids), id="wa_admins"
        )
        yield Static(self.tr("whatsapp.inbound_intro"))
        yield Label(self.tr("whatsapp.inbound_label"))
        yield Switch(value=self.state.whatsapp_inbound_enabled, id="wa_inbound")
        yield Label(self.tr("whatsapp.allow_label"))
        yield Input(value=" ".join(self.state.whatsapp_allow), id="wa_allow")
        yield Label(self.tr("whatsapp.role_label"))
        yield Input(value=self.state.whatsapp_role, id="wa_role")

    def on_mount(self) -> None:
        self._refresh_enabled()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id in ("use_whatsapp", "wa_inbound"):
            self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        wa_enabled = self.query_one("#use_whatsapp", Switch).value
        for field_id in ("bridge_path", "bridge_port", "callback_port", "wa_admins", "wa_inbound"):
            self.query_one(f"#{field_id}").disabled = not wa_enabled
        inbound_on = wa_enabled and self.query_one("#wa_inbound", Switch).value
        for field_id in ("wa_allow", "wa_role"):
            self.query_one(f"#{field_id}", Input).disabled = not inbound_on

    def save(self) -> str | None:
        use = self.query_one("#use_whatsapp", Switch).value
        self.state.use_whatsapp = use
        if not use:
            # Telegram-only: skip the bridge fields entirely. Keep whatever values
            # are present (defaults are fine); the runtime ignores them when off.
            self.state.whatsapp_inbound_enabled = False
            self.state.whatsapp_admin_chat_ids = []
            return None

        path = self.query_one("#bridge_path", Input).value
        bridge_port = self.query_one("#bridge_port", Input).value
        callback_port = self.query_one("#callback_port", Input).value
        if err := validate_required(path, self.lang):
            return self.tr("whatsapp.err.path", err=err)
        if err := validate_port(bridge_port, self.lang):
            return self.tr("whatsapp.err.bridge_port", err=err)
        if err := validate_port(callback_port, self.lang):
            return self.tr("whatsapp.err.callback_port", err=err)
        if bridge_port.strip() == callback_port.strip():
            return self.tr("whatsapp.err.ports_differ")
        self.state.whatsapp_bridge_path = path.strip()
        self.state.whatsapp_bridge_port = int(bridge_port)
        self.state.octoops_callback_port = int(callback_port)
        self.state.whatsapp_admin_chat_ids = parse_id_list(
            self.query_one("#wa_admins", Input).value
        )

        inbound = self.query_one("#wa_inbound", Switch).value
        self.state.whatsapp_inbound_enabled = inbound
        if inbound:
            role = self.query_one("#wa_role", Input).value.strip().lower() or "operator"
            if err := validate_role(role, self.lang):
                return self.tr("whatsapp.err.inbound_role", err=err)
            allow = parse_id_list(self.query_one("#wa_allow", Input).value)
            if not allow:
                return self.tr("whatsapp.err.need_allow")
            self.state.whatsapp_allow = allow
            self.state.whatsapp_role = role
        else:
            self.state.whatsapp_allow = []
        return None
