"""Persistence for runtime role grants (Option A: layered permissions).

``config.toml`` is the immutable base. Grants made while the bot is running (via
the ``access`` module's ``/grant`` and ``/revoke``) are layered on top and stored
here — ``$OCTOOPS_HOME/data/access.json`` — so they survive restarts without ever
rewriting the operator's hand-edited config file.

A corrupt or unreadable store is treated as "no runtime grants" (log + continue)
rather than a fatal error: the config-declared base still authorizes the admins,
so the operator can always get back in and fix it.
"""

from __future__ import annotations

import json
from pathlib import Path

from octoops.core.logging import get_logger
from octoops.core.secure_io import write_private_text
from octoops.shared.models import Role

_log = get_logger("octoops.core.role_store")


class RoleStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, Role]:
        """Return {user_id: Role} for persisted runtime grants ({} if none/bad)."""
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            grants = raw.get("grants", {})
            return {str(uid): Role.from_str(role) for uid, role in grants.items()}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            _log.error("role_store.load_failed", path=str(self._path), error=str(exc))
            return {}

    def save(self, grants: dict[str, Role]) -> None:
        """Atomically write the runtime grants. Best-effort: errors are logged."""
        data = {"grants": {uid: role.name.lower() for uid, role in grants.items()}}
        try:
            # 0600: this file decides who can drive the bot — not world-readable.
            write_private_text(self._path, json.dumps(data, indent=2))
        except OSError as exc:
            _log.error("role_store.save_failed", path=str(self._path), error=str(exc))
