import json
import sys

from octoops.core.paths import AppPaths
from octoops.core.plugin_loader import _read_manifest, load_modules


def test_read_manifest_valid(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({"name": "foo", "version": "2.1.0", "description": "d"}))
    m = _read_manifest(p)
    assert m is not None
    assert (m.name, m.version, m.description) == ("foo", "2.1.0", "d")


def test_read_manifest_malformed_returns_none(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text("{ not valid json ")
    assert _read_manifest(p) is None


def test_read_manifest_missing_name_returns_none(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({"version": "1.0.0"}))
    assert _read_manifest(p) is None


def test_load_enabled_status_module(registry):
    # registry fixture enables ["status"]; loads the real built-in module.
    loaded = load_modules(registry)
    names = {m.registration.name for m in loaded}
    assert "status" in names


def test_disabled_module_is_skipped(registry):
    registry.config.enabled_modules = []  # nothing enabled
    loaded = load_modules(registry)
    assert loaded == []


def test_missing_modules_dir_returns_empty(registry, tmp_path):
    loaded = load_modules(registry, modules_dir=tmp_path / "does_not_exist")
    assert loaded == []


_EXT_MODULE = '''\
from octoops.core.contracts import CommandDef, ModuleRegistration
from octoops.shared.models import Role


async def _h(request, ctx):  # pragma: no cover - not dispatched here
    from octoops.shared.models import Response
    return Response(text="hi", chat_id=request.chat_id)


def load(ctx):
    return ModuleRegistration(
        name="extmod",
        commands=[CommandDef("extping", "d", Role.Viewer, _h)],
    )
'''


def test_external_dropin_module_loads(registry, tmp_path):
    mod_dir = tmp_path / "modules" / "extmod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "plugin.json").write_text(
        json.dumps({"name": "extmod", "version": "1.0.0", "description": "d"})
    )
    (mod_dir / "__init__.py").write_text(_EXT_MODULE)

    registry.paths = AppPaths(home=tmp_path)
    registry.config.enabled_modules = ["extmod"]  # isolate from built-ins

    try:
        loaded = load_modules(registry)
        names = {m.registration.name for m in loaded}
        assert "extmod" in names
    finally:
        sys.modules.pop("extmod", None)
        ext = str((tmp_path / "modules").resolve())
        if ext in sys.path:
            sys.path.remove(ext)
