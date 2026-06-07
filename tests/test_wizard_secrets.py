"""Wizard handling of module secrets (.env sidecar) and WhatsApp inbound config."""

from __future__ import annotations

import os
import stat
import tomllib

from octoops.core.config import (
    AppConfig,
    CoreConfig,
    TelegramConfig,
    TransportConfig,
)
from octoops.core.envfile import parse_env
from octoops.shared.models import Role
from octoops.wizard.state import WizardState, secret_env_name
from octoops.wizard.writer import (
    build_document,
    render_config,
    render_env,
    state_from_config,
    write_env,
)


def test_secret_env_name():
    assert secret_env_name("brain", "api_key") == "BRAIN_API_KEY"
    assert secret_env_name("my-mod", "api.key") == "MY_MOD_API_KEY"


# --- .env rendering / writing ------------------------------------------------


def test_render_env_roundtrips():
    state = WizardState(secrets={"BRAIN_API_KEY": "sk-abc", "OTHER": "x"})
    assert parse_env(render_env(state)) == {"BRAIN_API_KEY": "sk-abc", "OTHER": "x"}


def test_write_env_none_when_no_secrets(tmp_path):
    assert write_env(WizardState(), tmp_path / ".env") is None
    assert not (tmp_path / ".env").exists()


def test_write_env_writes_private_file(tmp_path):
    state = WizardState(secrets={"BRAIN_API_KEY": "sk-abc"})
    path = write_env(state, tmp_path / ".env")
    assert path is not None
    assert parse_env(path.read_text("utf-8")) == {"BRAIN_API_KEY": "sk-abc"}
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_secrets_never_land_in_config_toml():
    # A Password value lives in state.secrets, not module_config -> not in config.
    state = WizardState(
        bot_token="123456:ABC",
        admin_chat_id="1",
        enabled_modules=["brain"],
        module_config={"brain": {"model": "m"}},
        secrets={"BRAIN_API_KEY": "sk-should-not-appear"},
    )
    rendered = tomllib.loads(render_config(state))
    assert "sk-should-not-appear" not in str(rendered)
    assert "api_key" not in rendered.get("modules", {}).get("brain", {})


# --- WhatsApp inbound in the generated config --------------------------------


def test_build_document_emits_inbound_fields():
    state = WizardState(
        bot_token="123456:ABC",
        admin_chat_id="1",
        use_whatsapp=True,
        whatsapp_inbound_enabled=True,
        whatsapp_allow=["5511999998888"],
        whatsapp_command="ask",
        whatsapp_role="operator",
    )
    tr = build_document(state)["transport"]
    assert tr["whatsapp_inbound_enabled"] is True
    assert tr["whatsapp_allow"] == ["5511999998888"]
    assert tr["whatsapp_command"] == "ask"
    assert tr["whatsapp_role"] == "operator"


def test_inbound_forced_off_when_whatsapp_off():
    state = WizardState(
        bot_token="123456:ABC",
        admin_chat_id="1",
        use_whatsapp=False,
        whatsapp_inbound_enabled=True,  # contradictory; must be forced off
        whatsapp_allow=["5511999998888"],
    )
    tr = build_document(state)["transport"]
    assert tr["whatsapp_inbound_enabled"] is False


def test_state_from_config_hydrates_inbound():
    cfg = AppConfig(
        telegram=TelegramConfig(bot_token="t", admin_chat_id="1"),
        transport=TransportConfig(
            whatsapp_enabled=True,
            whatsapp_inbound_enabled=True,
            whatsapp_allow=["5511999998888"],
            whatsapp_command="ask",
            whatsapp_role=Role.Admin,
        ),
        core=CoreConfig(timezone="UTC"),
    )
    state = state_from_config(cfg)
    assert state.whatsapp_inbound_enabled is True
    assert state.whatsapp_allow == ["5511999998888"]
    assert state.whatsapp_command == "ask"
    assert state.whatsapp_role == "admin"
