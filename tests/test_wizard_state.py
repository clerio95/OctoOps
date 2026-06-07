from octoops.core.contracts import ConfigField, ConfigFieldKind
from octoops.wizard import state as S


def _field(kind, required=True):
    return ConfigField(
        key="k", label="K", description="d", required=required, default=None, kind=kind
    )


def test_parse_id_list_handles_commas_spaces_newlines():
    assert S.parse_id_list("1, 2  3\n4") == ["1", "2", "3", "4"]
    assert S.parse_id_list("  ") == []


def test_detect_timezone_returns_a_valid_zone():
    # Whatever it picks must be a real IANA zone (validators accept it).
    assert S.validate_timezone(S.detect_timezone()) is None


def test_detect_timezone_honors_tz_env(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    assert S.detect_timezone() == "America/New_York"


def test_detect_timezone_ignores_garbage_tz_env(monkeypatch):
    # A bogus TZ is skipped; detection falls back to a valid zone, never the junk.
    monkeypatch.setenv("TZ", "Not/AZone")
    result = S.detect_timezone()
    assert result != "Not/AZone"
    assert S.validate_timezone(result) is None


def test_wizard_state_default_timezone_is_valid():
    assert S.validate_timezone(S.WizardState().timezone) is None


def test_validate_has_authorized_user():
    # All empty -> blocked; any one list populated -> allowed.
    assert S.validate_has_authorized_user([], [], []) is not None
    assert S.validate_has_authorized_user(["1"], [], []) is None
    assert S.validate_has_authorized_user([], ["1"], []) is None
    assert S.validate_has_authorized_user([], [], ["1"]) is None


def test_validate_bot_token():
    assert S.validate_bot_token("") == "required"
    assert S.validate_bot_token("nocolon") is not None
    assert S.validate_bot_token("123456:ABC-def") is None


def test_validate_chat_id_allows_negative_groups():
    assert S.validate_chat_id("-1001234") is None
    assert S.validate_chat_id("123") is None
    assert S.validate_chat_id("abc") is not None
    assert S.validate_chat_id("") == "required"


def test_validate_user_id_list():
    assert S.validate_user_id_list("123 456") is None
    assert S.validate_user_id_list("123 abc") is not None
    assert S.validate_user_id_list("") is None  # empty list is allowed


def test_validate_timezone():
    assert S.validate_timezone("America/Sao_Paulo") is None
    assert S.validate_timezone("Mars/Phobos") is not None


def test_validate_port():
    assert S.validate_port("3000") is None
    assert S.validate_port("0") is not None
    assert S.validate_port("70000") is not None
    assert S.validate_port("x") is not None


def test_validate_role():
    assert S.validate_role("admin") is None
    assert S.validate_role("ADMIN") is None
    assert S.validate_role("superuser") is not None


def test_validate_config_field_by_kind():
    assert S.validate_config_field(_field(ConfigFieldKind.Integer), "12") is None
    assert S.validate_config_field(_field(ConfigFieldKind.Integer), "x") is not None
    assert S.validate_config_field(_field(ConfigFieldKind.IpAddress), "10.0.0.1") is None
    assert S.validate_config_field(_field(ConfigFieldKind.IpAddress), "999.1.1.1") is not None
    assert S.validate_config_field(_field(ConfigFieldKind.Boolean), "true") is None
    assert S.validate_config_field(_field(ConfigFieldKind.Boolean), "maybe") is not None


def test_validate_config_field_optional_empty_ok():
    assert S.validate_config_field(_field(ConfigFieldKind.Text, required=False), "") is None
    assert S.validate_config_field(_field(ConfigFieldKind.Text, required=True), "") == "required"


def test_coerce_config_value():
    assert S.coerce_config_value(_field(ConfigFieldKind.Integer), "42") == 42
    assert S.coerce_config_value(_field(ConfigFieldKind.Boolean), "yes") is True
    assert S.coerce_config_value(_field(ConfigFieldKind.Boolean), "false") is False
    assert S.coerce_config_value(_field(ConfigFieldKind.Text), "hello") == "hello"
