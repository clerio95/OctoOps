from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Input, Label, Select

from octoops.wizard.screens.base import BaseStep
from octoops.wizard.state import (
    parse_id_list,
    validate_has_authorized_user,
    validate_required,
    validate_role,
    validate_timezone,
    validate_user_id_list,
)


class CoreSettingsStep(BaseStep):
    STEP_ID = "core"
    step_title = "Core settings"

    def content(self) -> ComposeResult:
        yield Label("Timezone (IANA, e.g. America/Sao_Paulo)")
        yield Input(value=self.state.timezone, id="timezone")
        yield Label("Allowed Telegram user IDs (space/comma separated)")
        yield Input(value=" ".join(self.state.allowed_user_ids), id="allowed")
        yield Label("Operator user IDs")
        yield Input(value=" ".join(self.state.operator_user_ids), id="operators")
        yield Label("Admin user IDs")
        yield Input(value=" ".join(self.state.admin_user_ids), id="admins")
        yield Label("Default role (for allowed users)")
        yield Select(
            [("viewer", "viewer"), ("operator", "operator"), ("admin", "admin")],
            value=self.state.default_role,
            allow_blank=False,
            id="default_role",
        )
        yield Label("Log file path")
        yield Input(value=self.state.log_file, id="log_file")

    def save(self) -> str | None:
        tz = self.query_one("#timezone", Input).value
        allowed = self.query_one("#allowed", Input).value
        operators = self.query_one("#operators", Input).value
        admins = self.query_one("#admins", Input).value
        role = str(self.query_one("#default_role", Select).value)
        log_file = self.query_one("#log_file", Input).value

        if err := validate_timezone(tz):
            return err
        for label, raw in (("Allowed", allowed), ("Operator", operators), ("Admin", admins)):
            if err := validate_user_id_list(raw):
                return f"{label} IDs: {err}"
        if err := validate_role(role):
            return f"Default role: {err}"
        if err := validate_required(log_file):
            return f"Log file: {err}"

        allowed_ids = parse_id_list(allowed)
        operator_ids = parse_id_list(operators)
        admin_ids = parse_id_list(admins)
        if err := validate_has_authorized_user(allowed_ids, operator_ids, admin_ids):
            return err

        self.state.timezone = tz.strip()
        self.state.allowed_user_ids = allowed_ids
        self.state.operator_user_ids = operator_ids
        self.state.admin_user_ids = admin_ids
        self.state.default_role = role.strip().lower()
        self.state.log_file = log_file.strip()
        return None
