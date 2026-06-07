from pathlib import Path

from octoops.core.paths import ENV_HOME, AppPaths, resolve_home


def test_env_home_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_HOME, str(tmp_path))
    assert resolve_home("/somewhere/else/config.toml") == tmp_path.resolve()


def test_home_from_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_HOME, raising=False)
    cfg = tmp_path / "config.toml"
    assert resolve_home(cfg) == tmp_path.resolve()


def test_home_defaults_to_cwd(monkeypatch):
    monkeypatch.delenv(ENV_HOME, raising=False)
    assert resolve_home(None) == Path.cwd().resolve()


def test_resolve_relative_against_home(tmp_path):
    paths = AppPaths(home=tmp_path)
    assert paths.resolve("logs/octoops.log") == tmp_path / "logs" / "octoops.log"


def test_resolve_absolute_passthrough(tmp_path):
    paths = AppPaths(home=tmp_path)
    abs_path = Path("/var/log/octoops.log")
    assert paths.resolve(abs_path) == abs_path


def test_derived_dirs(tmp_path):
    paths = AppPaths(home=tmp_path)
    assert paths.logs == tmp_path / "logs"
    assert paths.data == tmp_path / "data"
    assert paths.modules == tmp_path / "modules"
