from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Input, Label, Static

from octoops.core.contracts import ConfigField, ConfigFieldKind
from octoops.wizard.screens.base import BaseStep
from octoops.wizard.state import (
    coerce_config_value,
    secret_env_name,
    validate_config_field,
)


def _field_id(module: str, key: str) -> str:
    return f"cfg__{module}__{key}"


class ModuleConfigStep(BaseStep):
    STEP_ID = "module_config"
    title_key = "module_config.title"

    def content(self) -> ComposeResult:
        # (module, ConfigField, input_id) for save() to read back.
        self._fields: list[tuple[str, ConfigField, str]] = []
        for module in self.wizard_app.enabled_with_fields():
            name = module.manifest.name
            assert module.registration is not None
            yield Static(f"[{name}]", classes="step-title")
            existing = self.state.module_config.get(name, {})
            for field_def in module.registration.config_fields:
                req = self.tr(
                    "module_config.required"
                    if field_def.required
                    else "module_config.optional"
                )
                yield Label(
                    self.tr(
                        "module_config.field_label",
                        label=field_def.label,
                        req=req,
                        description=field_def.description,
                    )
                )
                if field_def.kind is ConfigFieldKind.Password:
                    # Secrets live in state.secrets (-> .env), not module_config.
                    prefill = self.state.secrets.get(
                        secret_env_name(name, field_def.key), ""
                    )
                else:
                    prefill = existing.get(field_def.key, field_def.default or "")
                input_id = _field_id(name, field_def.key)
                yield Input(
                    value=str(prefill) if prefill is not None else "",
                    password=field_def.kind is ConfigFieldKind.Password,
                    placeholder=field_def.default or "",
                    id=input_id,
                )
                self._fields.append((name, field_def, input_id))

    def save(self) -> str | None:
        collected: dict[str, dict[str, object]] = {}
        for module_name, field_def, input_id in self._fields:
            raw = self.query_one(f"#{input_id}", Input).value
            if err := validate_config_field(field_def, raw, self.lang):
                return self.tr(
                    "module_config.err",
                    module=module_name,
                    key=field_def.key,
                    err=err,
                )
            if field_def.kind is ConfigFieldKind.Password:
                # Route secrets to the .env sidecar, never into config.toml.
                env_name = secret_env_name(module_name, field_def.key)
                value = raw.strip()
                if value:
                    self.state.secrets[env_name] = value
                else:
                    self.state.secrets.pop(env_name, None)
                continue
            value = coerce_config_value(field_def, raw)
            if value is not None:
                collected.setdefault(module_name, {})[field_def.key] = value
        # Replace config only for the modules shown here.
        for module_name in {m for m, _f, _i in self._fields}:
            self.state.module_config[module_name] = collected.get(module_name, {})
        return None
