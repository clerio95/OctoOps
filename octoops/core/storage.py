"""Per-module JSON persistence with the safety conventions built in.

Every stateful module needs the same three guarantees, and hand-rolling them
per module is how subtle data-loss bugs creep in:

- atomic saves (write a temp file, then ``os.replace``) so a crash mid-write
  can never truncate the store;
- forgiving loads (missing file -> default) so the bot keeps running;
- quarantine-on-corrupt (the unparseable file is renamed aside) so the next
  save can't silently destroy the operator's data.

Get one via ``ctx.store()`` (defaults to ``data/<module>.json``) and keep your
own schema inside it. Note the runtime is a single event loop: a synchronous
load-modify-save inside a command handler is effectively serialized. If you
ever move storage I/O to a thread or background task, add your own locking.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from octoops.core.logging import get_logger
from octoops.core.secure_io import quarantine_corrupt, write_private_text

_log = get_logger("octoops.core.storage")


class JsonStore:
    """One JSON document at a fixed path; load/save with the safety rails.

    ``private=True`` routes saves through the owner-only (0600) writer — use it
    when the document holds anything secret-adjacent.
    """

    def __init__(self, path: str | Path, *, private: bool = False) -> None:
        self._path = Path(path)
        self._private = private

    @property
    def path(self) -> Path:
        return self._path

    def load(self, default: Any = None) -> Any:
        """Parsed JSON, or ``default`` when missing/unreadable/corrupt.

        A corrupt file is quarantined (renamed to ``<name>.corrupt-<ts>``)
        before ``default`` is returned, so a later save() can't overwrite the
        original data with the empty view.
        """
        if not self._path.exists():
            return default
        try:
            return json.loads(self._path.read_text("utf-8"))
        except OSError as exc:
            # Couldn't read — the file itself may be fine, so leave it in place.
            _log.error("storage.read_failed", path=str(self._path), error=str(exc))
            return default
        except ValueError as exc:
            quarantined = quarantine_corrupt(self._path)
            _log.error(
                "storage.corrupt_quarantined",
                path=str(self._path),
                quarantined=str(quarantined),
                error=str(exc),
            )
            return default

    def save(self, data: Any) -> None:
        """Atomically write ``data`` as pretty UTF-8 JSON (parents created)."""
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if self._private:
            write_private_text(self._path, text)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self._path)
