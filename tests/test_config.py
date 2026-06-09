import pytest

from octoops.core.config import AppConfig
from octoops.core.errors import ConfigError
from octoops.core.registry import ModuleConfig
from octoops.shared.models import Role

VALID = {
    "telegram": {"bot_token": "tok", "admin_chat_id": "1"},
    "core": {
        "timezone": "America/Sao_Paulo",
        "allowed_user_ids": [123, "456"],
        "admin_user_ids": ["123"],
        "default_role": "operator",
    },
    "modules": {
        "enabled": ["status", "widget"],
        "widget": {"device_ip": "192.168.0.1"},
    },
}


def test_load_valid_config():
    cfg = AppConfig.from_dict(VALID)
    assert cfg.telegram.bot_token == "tok"
    assert cfg.core.default_role is Role.Operator
    # IDs are normalized to strings.
    assert cfg.core.allowed_user_ids == ["123", "456"]
    assert cfg.enabled_modules == ["status", "widget"]
    assert cfg.module_config("widget") == {"device_ip": "192.168.0.1"}
    assert cfg.module_config("missing") == {}


def test_whatsapp_enabled_defaults_true_when_absent():
    # Configs predating the flag keep building the WhatsApp transport.
    cfg = AppConfig.from_dict(VALID)
    assert cfg.transport.whatsapp_enabled is True


def test_whatsapp_can_be_disabled():
    data = {**VALID, "transport": {"whatsapp_enabled": False}}
    cfg = AppConfig.from_dict(data)
    assert cfg.transport.whatsapp_enabled is False


def test_missing_bot_token_raises():
    data = {"telegram": {"admin_chat_id": "1"}, "core": {"timezone": "UTC"}}
    with pytest.raises(ConfigError):
        AppConfig.from_dict(data)


def test_language_defaults_to_en():
    cfg = AppConfig.from_dict(VALID)
    assert cfg.core.language == "en"


def test_language_parsed_and_persisted():
    data = {**VALID, "core": {**VALID["core"], "language": "pt-BR"}}
    cfg = AppConfig.from_dict(data)
    assert cfg.core.language == "pt-BR"


def test_missing_timezone_raises():
    data = {"telegram": {"bot_token": "t", "admin_chat_id": "1"}, "core": {}}
    with pytest.raises(ConfigError):
        AppConfig.from_dict(data)


def test_mcp_defaults_when_absent():
    cfg = AppConfig.from_dict(VALID)
    assert cfg.mcp.enabled is False
    assert cfg.mcp.host == "127.0.0.1"
    assert cfg.mcp.service_role is Role.Viewer
    assert cfg.mcp.token is None


def test_mcp_parsed():
    data = dict(VALID)
    data["mcp"] = {
        "enabled": True,
        "port": 4000,
        "service_role": "operator",
        "allow_command_execution": True,
        "token": "abc",
    }
    cfg = AppConfig.from_dict(data)
    assert cfg.mcp.enabled is True
    assert cfg.mcp.port == 4000
    assert cfg.mcp.service_role is Role.Operator
    assert cfg.mcp.allow_command_execution is True
    assert cfg.mcp.token == "abc"


def test_mcp_invalid_role_raises():
    data = dict(VALID)
    data["mcp"] = {"service_role": "wizard"}
    with pytest.raises(ConfigError):
        AppConfig.from_dict(data)


def test_mcp_execution_without_token_raises():
    data = dict(VALID)
    data["mcp"] = {"enabled": True, "allow_command_execution": True}
    with pytest.raises(ConfigError, match="requires a token"):
        AppConfig.from_dict(data)


def test_mcp_execution_with_token_ok():
    data = dict(VALID)
    data["mcp"] = {
        "enabled": True,
        "allow_command_execution": True,
        "token": "s3cret",
    }
    cfg = AppConfig.from_dict(data)
    assert cfg.mcp.allow_command_execution is True


def test_mcp_non_loopback_without_token_raises():
    data = dict(VALID)
    data["mcp"] = {"enabled": True, "host": "0.0.0.0"}
    with pytest.raises(ConfigError, match="not loopback"):
        AppConfig.from_dict(data)


def test_mcp_non_loopback_with_token_ok():
    data = dict(VALID)
    data["mcp"] = {"enabled": True, "host": "0.0.0.0", "token": "s3cret"}
    cfg = AppConfig.from_dict(data)
    assert cfg.mcp.host == "0.0.0.0"


def test_mcp_disabled_skips_validation():
    # A risky combo is tolerated while the server is off (never started).
    data = dict(VALID)
    data["mcp"] = {"enabled": False, "allow_command_execution": True}
    cfg = AppConfig.from_dict(data)
    assert cfg.mcp.enabled is False


def test_is_secret_field_name():
    from octoops.core.config import is_secret_field_name

    for name in ("api_key", "API_KEY", "token", "client_secret", "db_password"):
        assert is_secret_field_name(name)
    for name in ("device_ip", "model", "retries", "host"):
        assert not is_secret_field_name(name)


def test_module_secret_values_collects_only_secret_strings():
    from octoops.core.config import module_secret_values

    sections = {
        "brain": {"api_key": "sk-secret", "model": "gpt", "max_tokens": 10},
        "widget": {"device_ip": "192.168.0.1"},
        "bad": "not-a-dict",
    }
    assert module_secret_values(sections) == ["sk-secret"]


def test_module_config_require_missing_key():
    mc = ModuleConfig({"present": "yes"})
    assert mc.require("present") == "yes"
    assert mc.get("absent") is None
    with pytest.raises(ConfigError):
        mc.require("absent")
