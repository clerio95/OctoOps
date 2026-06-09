import os
import stat
import tomllib

from octoops.core.config import AppConfig
from octoops.wizard.state import WizardState
from octoops.wizard.writer import render_config, state_from_config, write_config


def _state() -> WizardState:
    return WizardState(
        bot_token="123456:ABC-def",
        admin_chat_id="999",
        timezone="America/Sao_Paulo",
        allowed_user_ids=["100", "200"],
        admin_user_ids=["100"],
        default_role="viewer",
        enabled_modules=["status", "widget"],
        module_config={"widget": {"device_ip": "192.168.0.5", "retries": 3, "verbose": True}},
        whatsapp_bridge_port=3000,
        octoops_callback_port=3001,
    )


def test_render_is_valid_toml_with_mcp_section():
    data = tomllib.loads(render_config(_state()))
    assert data["telegram"]["bot_token"] == "123456:ABC-def"
    assert data["core"]["allowed_user_ids"] == ["100", "200"]
    assert data["modules"]["enabled"] == ["status", "widget"]
    assert data["modules"]["widget"] == {
        "device_ip": "192.168.0.5",
        "retries": 3,
        "verbose": True,
    }
    # MCP section pre-provisioned and disabled, with full defaults.
    assert data["mcp"]["enabled"] is False
    assert data["mcp"]["host"] == "127.0.0.1"
    assert data["mcp"]["port"] == 3002
    assert data["mcp"]["service_role"] == "viewer"
    assert data["mcp"]["allow_command_execution"] is False


def test_language_written_and_roundtrips(tmp_path):
    state = _state()
    state.language = "pt-BR"
    path = tmp_path / "config.toml"
    write_config(state, path)
    cfg = AppConfig.load(path)
    assert cfg.core.language == "pt-BR"
    # config -> WizardState preserves the persisted language for a re-run.
    assert state_from_config(cfg).language == "pt-BR"


def test_language_defaults_to_en_in_render():
    data = tomllib.loads(render_config(_state()))
    assert data["core"]["language"] == "en"


def test_roundtrips_through_appconfig(tmp_path):
    path = tmp_path / "config.toml"
    write_config(_state(), path)
    cfg = AppConfig.load(path)  # must not raise
    assert cfg.telegram.bot_token == "123456:ABC-def"
    assert cfg.core.timezone == "America/Sao_Paulo"
    assert cfg.enabled_modules == ["status", "widget"]
    assert cfg.module_config("widget")["device_ip"] == "192.168.0.5"
    assert cfg.module_config("widget")["retries"] == 3


def test_preview_redacts_bot_token_but_keeps_bot_id():
    preview = render_config(_state(), redact_secrets=True)
    # Public bot id (before ':') is kept; the secret half is masked.
    assert "123456:" in preview
    assert "123456:ABC-def" not in preview


def test_preview_redacts_mcp_token():
    state = _state()
    state.mcp_section = {"enabled": True, "token": "supersecret"}
    preview = render_config(state, redact_secrets=True)
    assert "supersecret" not in preview


def test_preview_redacts_module_secret_in_config():
    # A secret hand-placed under [modules.<name>] (e.g. api_key fallback) is
    # masked in the preview but a non-secret field (device_ip) is shown.
    state = _state()
    state.module_config = {"brain": {"api_key": "sk-leakme", "model": "x"}}
    preview = render_config(state, redact_secrets=True)
    assert "sk-leakme" not in preview
    assert "model" in preview


def test_written_file_keeps_module_secret(tmp_path):
    # The file on disk is never redacted — the fallback must remain usable.
    state = _state()
    state.module_config = {"brain": {"api_key": "sk-leakme"}}
    path = tmp_path / "config.toml"
    write_config(state, path)
    assert AppConfig.load(path).module_config("brain")["api_key"] == "sk-leakme"


def test_written_file_keeps_real_secret_and_is_private(tmp_path):
    path = tmp_path / "config.toml"
    write_config(_state(), path)
    # The file on disk is NOT redacted...
    assert AppConfig.load(path).telegram.bot_token == "123456:ABC-def"
    # ...but it is owner-only on POSIX.
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_whatsapp_disabled_roundtrips(tmp_path):
    # A Telegram-only setup writes whatsapp_enabled=false and survives re-hydration.
    state = _state()
    state.use_whatsapp = False
    path = tmp_path / "config.toml"
    write_config(state, path)
    cfg = AppConfig.load(path)
    assert cfg.transport.whatsapp_enabled is False
    assert state_from_config(cfg).use_whatsapp is False


def test_whatsapp_enabled_roundtrips(tmp_path):
    state = _state()
    state.use_whatsapp = True
    path = tmp_path / "config.toml"
    write_config(state, path)
    cfg = AppConfig.load(path)
    assert cfg.transport.whatsapp_enabled is True
    assert state_from_config(cfg).use_whatsapp is True


def test_state_from_config_roundtrip(tmp_path):
    # config -> WizardState must reproduce every field the wizard manages, with
    # module-config value types (int/bool) preserved verbatim.
    path = tmp_path / "config.toml"
    write_config(_state(), path)
    state = state_from_config(AppConfig.load(path))

    assert state.bot_token == "123456:ABC-def"
    assert state.admin_chat_id == "999"
    assert state.allowed_user_ids == ["100", "200"]
    assert state.default_role == "viewer"
    assert state.enabled_modules == ["status", "widget"]
    assert state.module_config["widget"]["retries"] == 3
    assert state.module_config["widget"]["verbose"] is True


def test_state_from_config_preserves_enabled_mcp(tmp_path):
    # A re-run must not silently disable an MCP server the operator turned on.
    state = _state()
    state.mcp_section = {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 9000,
        "service_role": "operator",
        "allow_command_execution": True,
        "token": "secret",
    }
    first = tmp_path / "config.toml"
    write_config(state, first)
    assert AppConfig.load(first).mcp.enabled is True

    # Hydrate as if re-running setup, write again, and confirm MCP survives.
    hydrated = state_from_config(AppConfig.load(first))
    second = tmp_path / "config2.toml"
    write_config(hydrated, second)
    cfg = AppConfig.load(second)
    assert cfg.mcp.enabled is True
    assert cfg.mcp.host == "0.0.0.0"
    assert cfg.mcp.port == 9000
    assert cfg.mcp.allow_command_execution is True
    assert cfg.mcp.token == "secret"


def test_fresh_install_still_emits_disabled_mcp_default():
    # No mcp_section -> the commented, disabled default block (unchanged behavior).
    data = tomllib.loads(render_config(_state()))
    assert data["mcp"]["enabled"] is False
    assert data["mcp"]["service_role"] == "viewer"
