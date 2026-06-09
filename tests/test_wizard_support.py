"""Task Scheduler XML, module pre-scan, and pairing poll loop."""

import json

import pytest

from octoops.core.plugin_loader import discover_modules
from octoops.wizard import task_scheduler as ts
from octoops.wizard.pairing import wait_for_login


# --- task scheduler -----------------------------------------------------------


def test_build_task_xml_contents():
    xml = ts.build_task_xml(r"C:\octoops\.venv\Scripts\python.exe", "-m octoops", r"C:\octoops")
    assert "<BootTrigger>" in xml
    assert "S-1-5-18" in xml  # SYSTEM
    assert "<Count>10</Count>" in xml and "PT1M" in xml  # restart 10x / 1 min
    assert "-m octoops" in xml
    assert r"C:\octoops" in xml


def test_xml_escapes_special_chars():
    xml = ts.build_task_xml("py & q", "-m octoops <x>", "dir")
    assert "&amp;" in xml and "&lt;x&gt;" in xml


def test_register_task_noop_off_windows(monkeypatch):
    monkeypatch.setattr(ts, "is_windows", lambda: False)
    ok, msg = ts.register_task("python", "/base")
    assert ok is False
    assert "Windows-only" in msg


def test_write_run_bat_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "is_windows", lambda: True)
    bat = ts.write_run_bat(tmp_path, r"C:\Python\python.exe")
    assert bat is not None and bat.is_file()
    content = bat.read_text()
    assert r"C:\Python\python.exe" in content
    assert "octoops-stdout.log" in content
    assert "mkdir logs" in content
    # Truncate-on-start (single '>'), not append — bounds the raw stdout log.
    assert ">>" not in content


def test_write_run_bat_noop_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "is_windows", lambda: False)
    assert ts.write_run_bat(tmp_path, "python") is None
    assert not (tmp_path / "run.bat").exists()


def test_write_uninstall_bat_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "is_windows", lambda: True)
    bat = ts.write_uninstall_bat(tmp_path)
    assert bat is not None and bat.is_file()
    content = bat.read_text()
    assert "schtasks /Delete" in content
    assert ts.TASK_NAME in content
    assert "pause" in content


def test_write_uninstall_bat_noop_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "is_windows", lambda: False)
    assert ts.write_uninstall_bat(tmp_path) is None
    assert not (tmp_path / "uninstall.bat").exists()


# --- module pre-scan ----------------------------------------------------------


def test_discover_builtin_status():
    found = {m.manifest.name: m for m in discover_modules()}
    assert "status" in found
    reg = found["status"].registration
    assert reg is not None
    assert any(c.name == "status" for c in reg.commands)


def test_discover_external_module_with_config_fields(tmp_path):
    mod = tmp_path / "widget"
    mod.mkdir()
    (mod / "plugin.json").write_text(
        json.dumps({"name": "widget", "version": "1.0.0", "description": "d"})
    )
    (mod / "__init__.py").write_text(
        "from octoops.core.contracts import (ModuleRegistration, ConfigField,\n"
        "    ConfigFieldKind)\n"
        "def load(ctx):\n"
        "    return ModuleRegistration(name='widget', config_fields=[\n"
        "        ConfigField('device_ip','Device IP','d',True,None,ConfigFieldKind.IpAddress)])\n"
    )
    import sys

    try:
        found = {m.manifest.name: m for m in discover_modules(external_dir=tmp_path)}
        assert "widget" in found
        reg = found["widget"].registration
        assert reg is not None
        assert reg.config_fields[0].key == "device_ip"
    finally:
        sys.modules.pop("widget", None)
        ext = str(tmp_path.resolve())
        if ext in sys.path:
            sys.path.remove(ext)


def test_discover_records_error_for_broken_module(tmp_path):
    mod = tmp_path / "broken"
    mod.mkdir()
    (mod / "plugin.json").write_text(json.dumps({"name": "broken", "version": "1.0.0"}))
    (mod / "__init__.py").write_text("raise RuntimeError('boom on import')\n")
    import sys

    try:
        found = {m.manifest.name: m for m in discover_modules(external_dir=tmp_path)}
        assert found["broken"].registration is None
        assert "boom" in (found["broken"].error or "")
    finally:
        sys.modules.pop("broken", None)
        ext = str(tmp_path.resolve())
        if ext in sys.path:
            sys.path.remove(ext)


# --- pairing poll -------------------------------------------------------------


class _FakeClient:
    def __init__(self, logged_in_after: int):
        self._calls = 0
        self._after = logged_in_after

    async def health(self):
        self._calls += 1
        return {"ok": True, "logged_in": self._calls >= self._after}


@pytest.mark.asyncio
async def test_wait_for_login_succeeds_when_health_flips():
    client = _FakeClient(logged_in_after=2)
    assert await wait_for_login(client, timeout=5, interval=0.01) is True


@pytest.mark.asyncio
async def test_wait_for_login_times_out():
    client = _FakeClient(logged_in_after=999)
    assert await wait_for_login(client, timeout=0.05, interval=0.01) is False
