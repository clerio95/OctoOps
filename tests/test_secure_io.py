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
