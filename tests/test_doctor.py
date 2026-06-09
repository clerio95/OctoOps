from octoops.core.doctor import run_checks
from octoops.core.paths import AppPaths

VALID_CONFIG = """
[telegram]
bot_token = "tok"
admin_chat_id = "1"

[core]
timezone = "America/Sao_Paulo"
allowed_user_ids = ["1"]
admin_user_ids = ["1"]
log_file = "logs/octoops.log"

[modules]
enabled = ["status"]
"""


def test_check_passes_with_valid_config(tmp_path, capsys):
    (tmp_path / "config.toml").write_text(VALID_CONFIG)
    code = run_checks(tmp_path / "config.toml", AppPaths(home=tmp_path))
    out = capsys.readouterr().out
    assert code == 0
    assert "config.toml" in out
    assert "America/Sao_Paulo" in out
    # bridge is absent -> warning, but not a hard failure
    assert "whatsmeow bridge" in out


def test_check_missing_config_warns_not_fails(tmp_path, capsys):
    code = run_checks(tmp_path / "config.toml", AppPaths(home=tmp_path))
    assert code == 0  # missing config is a warning, not a hard failure
    assert "not found" in capsys.readouterr().out


def test_check_bad_timezone_fails(tmp_path):
    bad = VALID_CONFIG.replace("America/Sao_Paulo", "Mars/Phobos")
    (tmp_path / "config.toml").write_text(bad)
    code = run_checks(tmp_path / "config.toml", AppPaths(home=tmp_path))
    assert code == 1


# --- live token validation (#8) ----------------------------------------------


class _FakeApi:
    """Stand-in for telegram_pairing.TelegramApi with a scripted getMe."""

    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises

    def __call__(self, token):  # used as the api_factory
        self.token = token
        return self

    async def get_me(self):
        if self._raises is not None:
            raise self._raises
        return self._response

    async def close(self):
        pass


def test_no_live_check_by_default_stays_offline(tmp_path, capsys):
    # Without verify_token, the live check never runs (offline-safe).
    (tmp_path / "config.toml").write_text(VALID_CONFIG)
    boom = _FakeApi(raises=AssertionError("must not be called"))
    code = run_checks(tmp_path / "config.toml", AppPaths(home=tmp_path), api_factory=boom)
    assert code == 0
    assert "telegram token (live)" not in capsys.readouterr().out


def test_live_check_ok_passes(tmp_path, capsys):
    (tmp_path / "config.toml").write_text(VALID_CONFIG)
    api = _FakeApi(response={"ok": True, "result": {"username": "MyBot"}})
    code = run_checks(
        tmp_path / "config.toml", AppPaths(home=tmp_path),
        verify_token=True, api_factory=api,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "telegram token (live)" in out and "@MyBot" in out
    assert api.token == "tok"  # the configured token was used


def test_live_check_rejected_token_fails(tmp_path, capsys):
    (tmp_path / "config.toml").write_text(VALID_CONFIG)
    api = _FakeApi(response={"ok": False, "description": "Unauthorized"})
    code = run_checks(
        tmp_path / "config.toml", AppPaths(home=tmp_path),
        verify_token=True, api_factory=api,
    )
    assert code == 1
    assert "Unauthorized" in capsys.readouterr().out


def test_live_check_network_error_warns_not_fails(tmp_path, capsys):
    (tmp_path / "config.toml").write_text(VALID_CONFIG)
    api = _FakeApi(raises=OSError("no route to host"))
    code = run_checks(
        tmp_path / "config.toml", AppPaths(home=tmp_path),
        verify_token=True, api_factory=api,
    )
    # Network failure can't distinguish a bad token -> warning, exit 0.
    assert code == 0
    assert "couldn't reach Telegram" in capsys.readouterr().out


def test_whatsapp_session_pairing_state_reported(tmp_path, capsys):
    (tmp_path / "config.toml").write_text(VALID_CONFIG)

    code = run_checks(tmp_path / "config.toml", AppPaths(home=tmp_path))
    out = capsys.readouterr().out
    assert code == 0  # not paired is a warning, never a hard failure
    assert "WhatsApp session" in out and "not paired" in out

    (tmp_path / "whatsmeow.db").write_bytes(b"sqlite")
    run_checks(tmp_path / "config.toml", AppPaths(home=tmp_path))
    assert "paired (whatsmeow.db present)" in capsys.readouterr().out
