"""JsonStore: the module persistence primitive (atomic, forgiving, quarantining)."""

from __future__ import annotations

import os
import stat

from octoops.core.storage import JsonStore


def test_load_missing_returns_default(tmp_path):
    store = JsonStore(tmp_path / "nope.json")
    assert store.load(default=[]) == []
    assert store.load() is None


def test_save_load_roundtrip_creates_parents(tmp_path):
    store = JsonStore(tmp_path / "deep" / "dir" / "data.json")
    store.save({"itens": ["ação", 1]})  # non-ASCII survives (ensure_ascii=False)
    assert store.load() == {"itens": ["ação", 1]}
    assert "ação" in store.path.read_text("utf-8")


def test_save_is_atomic_no_tmp_left_behind(tmp_path):
    store = JsonStore(tmp_path / "data.json")
    store.save([1, 2])
    store.save([1, 2, 3])
    assert list(tmp_path.iterdir()) == [store.path]


def test_corrupt_file_is_quarantined_not_clobbered(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("[ not json", encoding="utf-8")
    store = JsonStore(path)

    assert store.load(default=[]) == []
    quarantined = list(tmp_path.glob("data.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text("utf-8") == "[ not json"

    store.save(["fresh"])  # the original bytes survive the new write
    assert quarantined[0].exists()
    assert store.load() == ["fresh"]


def test_private_store_writes_0600(tmp_path):
    store = JsonStore(tmp_path / "secret.json", private=True)
    store.save({"k": "v"})
    if os.name != "nt":  # POSIX mode bits only
        assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_ctx_store_defaults_to_module_name(registry, tmp_path):
    from octoops.core.paths import AppPaths
    from octoops.core.registry import ModuleConfig, ModuleContext

    registry.paths = AppPaths(home=tmp_path)
    ctx = ModuleContext(
        name="mymod",
        config=ModuleConfig({}),
        registry=registry,
        event_bus=registry.event_bus,
        scheduler=registry.scheduler,
    )
    store = ctx.store()
    assert store.path == tmp_path / "data" / "mymod.json"
    store.save({"x": 1})
    assert ctx.store().load() == {"x": 1}
    # A custom filename keeps the data dir.
    assert ctx.store("other.json").path == tmp_path / "data" / "other.json"
