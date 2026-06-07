"""run_wizard orchestration: discover -> (stubbed UI) -> write -> post-steps."""

from octoops.core.config import AppConfig
from octoops.core.paths import AppPaths
from octoops.wizard import run_wizard
from octoops.wizard.state import WizardState


def _good_state() -> WizardState:
    return WizardState(
        bot_token="123456:ABC-def",
        admin_chat_id="999",
        timezone="America/Sao_Paulo",
        allowed_user_ids=["100"],
        admin_user_ids=["100"],
        enabled_modules=["status"],
        use_whatsapp=True,  # exercise the pairing path...
        whatsapp_bridge_path="./whatsmeow-bridge.exe",  # ...which is absent in tmp -> skipped
        register_task=False,
    )


def test_run_wizard_writes_config(tmp_path, monkeypatch, capsys):
    import octoops.wizard.app as appmod

    monkeypatch.setattr(appmod.WizardApp, "run", lambda self: _good_state())
    cfg = tmp_path / "config.toml"
    written = run_wizard(str(cfg), AppPaths(home=tmp_path))

    assert written is True
    assert cfg.is_file()
    loaded = AppConfig.load(cfg)  # the written config is valid
    assert loaded.telegram.bot_token == "123456:ABC-def"
    assert "skipping pairing" in capsys.readouterr().out  # no bridge binary present


def test_run_wizard_cancelled_writes_nothing(tmp_path, monkeypatch):
    import octoops.wizard.app as appmod

    monkeypatch.setattr(appmod.WizardApp, "run", lambda self: None)
    cfg = tmp_path / "config.toml"
    written = run_wizard(str(cfg), AppPaths(home=tmp_path))

    assert written is False
    assert not cfg.exists()


def test_rerun_prefills_state_from_existing_config(tmp_path, monkeypatch):
    import octoops.wizard.app as appmod

    cfg = tmp_path / "config.toml"
    # First run writes a config from a known state.
    monkeypatch.setattr(appmod.WizardApp, "run", lambda self: _good_state())
    run_wizard(str(cfg), AppPaths(home=tmp_path))

    # Second run: the app echoes whatever state it was constructed with, which
    # must be hydrated from the just-written config (not blank defaults).
    captured: dict = {}

    def _echo(self):
        captured["state"] = self.state
        return self.state

    monkeypatch.setattr(appmod.WizardApp, "run", _echo)
    run_wizard(str(cfg), AppPaths(home=tmp_path))

    state = captured["state"]
    assert state.bot_token == "123456:ABC-def"
    assert state.admin_chat_id == "999"
    assert state.enabled_modules == ["status"]


def test_rerun_backs_up_previous_config(tmp_path, monkeypatch):
    import octoops.wizard.app as appmod

    monkeypatch.setattr(appmod.WizardApp, "run", lambda self: _good_state())
    cfg = tmp_path / "config.toml"
    run_wizard(str(cfg), AppPaths(home=tmp_path))  # first write, nothing to back up
    assert not list(tmp_path.glob("config.toml.*.bak"))

    run_wizard(str(cfg), AppPaths(home=tmp_path))  # overwrite -> backup taken
    backups = list(tmp_path.glob("config.toml.*.bak"))
    assert len(backups) == 1


def test_unparseable_existing_config_starts_fresh(tmp_path, monkeypatch, capsys):
    import octoops.wizard.app as appmod

    cfg = tmp_path / "config.toml"
    cfg.write_text("{ this is not valid toml", encoding="utf-8")

    captured: dict = {}

    def _echo(self):
        captured["state"] = self.state
        return self.state

    monkeypatch.setattr(appmod.WizardApp, "run", _echo)
    run_wizard(str(cfg), AppPaths(home=tmp_path))

    # No pre-filled token: a fresh blank state was used.
    assert captured["state"].bot_token == ""
    assert "starting fresh" in capsys.readouterr().out
