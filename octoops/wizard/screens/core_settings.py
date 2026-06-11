from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Input, Label, Select, Static

from octoops.wizard.screens.base import BaseStep
from octoops.wizard.state import (
    COMMON_TIMEZONES,
    parse_id_list,
    validate_has_authorized_user,
    validate_required,
    validate_role,
    validate_timezone,
    validate_user_id_list,
)

# Sentinel Select value meaning "use the custom text field below".
_TZ_CUSTOM = "__custom__"


class CoreSettingsStep(BaseStep):
    STEP_ID = "core"
    title_key = "core.title"

    def content(self) -> ComposeResult:
        # The current zone may be one not in the curated list (e.g. autodetected
        # on Linux or hand-entered on a re-run); offer it via the custom field.
        in_list = self.state.timezone in COMMON_TIMEZONES
        yield Label(self.tr("core.timezone"))
        yield Select(
            [(tz, tz) for tz in COMMON_TIMEZONES]
            + [(self.tr("core.tz_custom_option"), _TZ_CUSTOM)],
            value=self.state.timezone if in_list else _TZ_CUSTOM,
            allow_blank=False,
            id="timezone",
        )
        yield Label(self.tr("core.tz_custom_label"))
        yield Input(
            value="" if in_list else self.state.timezone,
            placeholder=self.tr("core.tz_placeholder"),
            id="timezone_custom",
        )
        yield Static(self.tr("core.userid_hint"), classes="warn", id="userid_hint")
        yield Label(self.tr("core.allowed_label"))
        yield Input(value=" ".join(self.state.allowed_user_ids), id="allowed")
        yield Label(self.tr("core.operators_label"))
        yield Input(value=" ".join(self.state.operator_user_ids), id="operators")
        yield Label(self.tr("core.admins_label"))
        yield Input(value=" ".join(self.state.admin_user_ids), id="admins")
        yield Label(self.tr("core.default_role"))
        yield Select(
            [("viewer", "viewer"), ("operator", "operator"), ("admin", "admin")],
            value=self.state.default_role,
            allow_blank=False,
            id="default_role",
        )
        yield Label(self.tr("core.log_file"))
        yield Input(value=self.state.log_file, id="log_file")

    def save(self) -> str | None:
        selected_tz = str(self.query_one("#timezone", Select).value)
        if selected_tz == _TZ_CUSTOM:
            tz = self.query_one("#timezone_custom", Input).value
        else:
            tz = selected_tz
        allowed = self.query_one("#allowed", Input).value
        operators = self.query_one("#operators", Input).value
        admins = self.query_one("#admins", Input).value
        role = str(self.query_one("#default_role", Select).value)
        log_file = self.query_one("#log_file", Input).value

        if err := validate_timezone(tz, self.lang):
            return err
        for label_key, raw in (
            ("core.label.allowed", allowed),
            ("core.label.operator", operators),
            ("core.label.admin", admins),
        ):
            if err := validate_user_id_list(raw, self.lang):
                return self.tr("core.err.ids", label=self.tr(label_key), err=err)
        if err := validate_role(role, self.lang):
            return self.tr("core.err.role", err=err)
        if err := validate_required(log_file, self.lang):
            return self.tr("core.err.log_file", err=err)

        allowed_ids = parse_id_list(allowed)
        operator_ids = parse_id_list(operators)
        admin_ids = parse_id_list(admins)
        if err := validate_has_authorized_user(
            allowed_ids, operator_ids, admin_ids, self.lang
        ):
            return err

        self.state.timezone = tz.strip()
        self.state.allowed_user_ids = allowed_ids
        self.state.operator_user_ids = operator_ids
        self.state.admin_user_ids = admin_ids
        self.state.default_role = role.strip().lower()
        self.state.log_file = log_file.strip()
        return None
