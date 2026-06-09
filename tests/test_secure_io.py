"""write_private_text: atomic, owner-only writes for secret-bearing files."""

from __future__ import annotations

import os
import stat

import pytest

from octoops.core.secure_io import write_private_text

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
def test_writes_file_0600(tmp_path):
    p = tmp_path / "data" / "secret.txt"  # parent created on demand
    write_private_text(p, "hello")
    assert p.read_text() == "hello"
    assert _mode(p) == 0o600


@posix_only
def test_tightens_a_preexisting_world_readable_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("old", encoding="utf-8")
    os.chmod(p, 0o644)
    write_private_text(p, "new")
    assert p.read_text() == "new"
    assert _mode(p) == 0o600


def test_replaces_atomically_no_tmp_left(tmp_path):
    p = tmp_path / "f.json"
    write_private_text(p, "{}")
    # The temp side-file must not linger after a successful write.
    assert not (tmp_path / "f.json.tmp").exists()
    assert list(tmp_path.iterdir()) == [p]


def test_returns_the_final_path(tmp_path):
    p = tmp_path / "f.txt"
    assert write_private_text(p, "x") == p


# --- harden_directory_acl (Windows NTFS lockdown) ------------------------------


def test_harden_acl_is_skipped_off_windows(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX-only assertion")
    from octoops.core.secure_io import harden_directory_acl

    ok, message = harden_directory_acl(tmp_path)
    assert not ok and "Windows-only" in message


def _force_windows(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "win32")


def test_harden_acl_builds_locked_down_icacls_command(monkeypatch, tmp_path):
    from octoops.core import secure_io

    _force_windows(monkeypatch)
    seen = {}

    def fake_run(cmd, capture_output, text):
        seen["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    ok, message = secure_io.harden_directory_acl(tmp_path, runner=fake_run)
    assert ok
    cmd = seen["cmd"]
    assert cmd[0] == "icacls" and cmd[1] == str(tmp_path)
    assert "/inheritance:r" in cmd  # broad inherited ACEs removed
    # SYSTEM (the scheduled task) and Administrators are granted by SID.
    grants = [cmd[i + 1] for i, part in enumerate(cmd) if part == "/grant:r"]
    assert any(g.startswith("*S-1-5-18:") for g in grants)
    assert any(g.startswith("*S-1-5-32-544:") for g in grants)


def test_harden_acl_reports_icacls_failure(monkeypatch, tmp_path):
    from octoops.core import secure_io

    _force_windows(monkeypatch)

    def failing_run(cmd, capture_output, text):
        class R:
            returncode = 5
            stderr = "Access is denied."
            stdout = ""

        return R()

    ok, message = secure_io.harden_directory_acl(tmp_path, runner=failing_run)
    assert not ok and "Access is denied" in message

    def missing_run(cmd, capture_output, text):
        raise FileNotFoundError

    ok, message = secure_io.harden_directory_acl(tmp_path, runner=missing_run)
    assert not ok and "icacls not found" in message


# --- quarantine_corrupt ---------------------------------------------------------


def test_quarantine_moves_file_aside(tmp_path):
    from octoops.core.secure_io import quarantine_corrupt

    p = tmp_path / "data.json"
    p.write_text("{ original bytes", encoding="utf-8")
    target = quarantine_corrupt(p)
    assert target is not None and target.exists()
    assert "corrupt-" in target.name
    assert not p.exists()  # moved, not copied
    assert target.read_text(encoding="utf-8") == "{ original bytes"


def test_quarantine_missing_file_returns_none(tmp_path):
    from octoops.core.secure_io import quarantine_corrupt

    assert quarantine_corrupt(tmp_path / "nope.json") is None
