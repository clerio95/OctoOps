"""Atomic, owner-only file writes for secret-bearing files.

config.toml (bot token), data/access.json (who has access), and data/invites.json
(nonces that grant a role) all hold material that must not be world- or
group-readable on a shared host. write_private_text creates the file 0600 *before*
any bytes are written — so the secret is never briefly readable — then atomically
replaces the target. POSIX mode bits are best-effort on Windows (NTFS uses ACLs).
"""

from __future__ import annotations

import os
from pathlib import Path

_PRIVATE_MODE = 0o600


def write_private_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Atomically write ``text`` to ``path`` with owner-only (0600) permissions.

    The temp file is opened 0600 up front (0600 has no group/other bits, so the
    umask can't loosen it), written, then ``os.replace``d into place carrying that
    mode. A partial temp is cleaned up on failure. Returns the final path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_MODE)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)  # atomic on POSIX/Windows; keeps the temp's 0600 mode
    # Belt-and-suspenders in case a pre-existing target or platform quirk left a
    # laxer mode. Best-effort: Windows chmod is limited.
    try:
        os.chmod(path, _PRIVATE_MODE)
    except OSError:
        pass
    return path
