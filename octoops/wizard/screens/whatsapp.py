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
    step_title = "WhatsApp bridge (optional output transport)"

    def content(self) -> ComposeResult:
        yield Static(
            "WhatsApp is an optional, output-only channel. Leave it off for a "
            "Telegram-only setup — you can enable it later by re-running setup."
        )
        yield Label("Enable WhatsApp output?")
        yield Switch(value=self.state.use_whatsapp, id="use_whatsapp")
        yield Label("Bridge binary path")
        yield Input(value=self.state.whatsapp_bridge_path, id="bridge_path")
        yield Label("Bridge port")
        yield Input(value=str(self.state.whatsapp_bridge_port), id="bridge_port", type="integer")
        yield Label("OctoOps callback port")
        yield Input(
            value=str(self.state.octoops_callback_port), id="callback_port", type="integer"
        )
        yield Label(
            "Admin WhatsApp numbers for the startup message (comma/space separated, "
            "digits only e.g. 5511999998888 — optional)"
        )
        yield Input(
            value=" ".join(self.state.whatsapp_admin_chat_ids), id="wa_admins"
        )
        yield Static(
            "Optional inbound: let whitelisted WhatsApp numbers message the brain "
            "(/ask). They can only ever reach the brain — never any other command."
        )
        yield Label("Enable inbound (whitelisted numbers → brain)?")
        yield Switch(value=self.state.whatsapp_inbound_enabled, id="wa_inbound")
        yield Label("Allowed WhatsApp numbers (comma/space separated)")
        yield Input(value=" ".join(self.state.whatsapp_allow), id="wa_allow")
        yield Label("Role for inbound users (viewer/operator/admin)")
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
        if err := validate_required(path):
            return f"Bridge path: {err}"
        if err := validate_port(bridge_port):
            return f"Bridge port: {err}"
        if err := validate_port(callback_port):
            return f"Callback port: {err}"
        if bridge_port.strip() == callback_port.strip():
            return "Bridge port and callback port must differ"
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
            if err := validate_role(role):
                return f"Inbound role: {err}"
            allow = parse_id_list(self.query_one("#wa_allow", Input).value)
            if not allow:
                return "Add at least one allowed WhatsApp number, or turn inbound off"
            self.state.whatsapp_allow = allow
            self.state.whatsapp_role = role
        else:
            self.state.whatsapp_allow = []
        return None
