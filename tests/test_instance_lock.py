"""Single-instance lock: OS-level, auto-released on death, holder info readable."""

from __future__ import annotations

import json
import os

from octoops.core.instance_lock import InstanceLock


def test_second_acquire_fails_while_held(tmp_path):
    path = tmp_path / "data" / "octoops.lock"  # parent created on demand
    first = InstanceLock(path)
    second = InstanceLock(path)

    assert first.acquire() is True
    assert second.acquire() is False  # held by a live process
    first.release()
    assert second.acquire() is True  # released -> takeable again
    second.release()


def test_holder_info_is_readable_while_locked(tmp_path):
    path = tmp_path / "octoops.lock"
    lock = InstanceLock(path)
    assert lock.acquire()
    try:
        info = InstanceLock(path).holder()
        assert info.get("pid") == os.getpid()
        assert "started" in info
    finally:
        lock.release()


def test_holder_is_empty_dict_when_unreadable(tmp_path):
    assert InstanceLock(tmp_path / "missing.lock").holder() == {}
    bad = tmp_path / "bad.lock"
    bad.write_text("not json", "utf-8")
    assert InstanceLock(bad).holder() == {}


def test_release_is_idempotent(tmp_path):
    lock = InstanceLock(tmp_path / "octoops.lock")
    assert lock.acquire()
    lock.release()
    lock.release()  # second release must not raise
