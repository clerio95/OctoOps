"""Base-directory resolution so the install is relocatable.

All runtime paths (config, logs, data, modules, bridge binary) resolve against a
single base directory instead of the process CWD. Resolution order:

1. ``OCTOOPS_HOME`` environment variable, if set.
2. The directory containing the config file, if one is given.
3. The current working directory.

This makes the whole folder copyable and keeps Task Scheduler's "Start in"
setting from being load-bearing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "OCTOOPS_HOME"


def resolve_home(config_path: str | Path | None = None) -> Path:
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env).expanduser().resolve()
    if config_path is not None:
        return Path(config_path).expanduser().resolve().parent
    return Path.cwd().resolve()


@dataclass(frozen=True)
class AppPaths:
    home: Path

    @classmethod
    def from_config(cls, config_path: str | Path | None = None) -> "AppPaths":
        return cls(home=resolve_home(config_path))

    def resolve(self, path: str | Path) -> Path:
        """Resolve a possibly-relative path against home. Absolute paths pass through."""
        p = Path(path).expanduser()
        return p if p.is_absolute() else (self.home / p)

    @property
    def data(self) -> Path:
        return self.home / "data"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def modules(self) -> Path:
        """External drop-in modules directory (next to config), if operators use it."""
        return self.home / "modules"
