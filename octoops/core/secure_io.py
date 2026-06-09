"""Atomic, owner-only file writes for secret-bearing files.

config.toml (bot token), data/access.json (who has access), and data/invites.json
(nonces that grant a role) all hold material that must not be world- or
group-readable on a shared host. write_private_text creates the file 0600 *before*
any bytes are written — so the secret is never briefly readable — then atomically
replaces the target. POSIX mode bits are best-effort on Windows (NTFS uses ACLs).
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_PRIVATE_MODE = 0o600

# Well-known SIDs (locale-proof — "Administrators" is "Administradores" on a
# pt-BR Windows). SYSTEM runs the Task Scheduler task, so it must keep access.
_SYSTEM_SID = "*S-1-5-18"
_ADMINISTRATORS_SID = "*S-1-5-32-544"


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


def quarantine_corrupt(path: str | Path) -> Path | None:
    """Move an unparseable data file aside as ``<name>.corrupt-<timestamp>``.

    The stores treat a corrupt file as empty so the bot keeps running — but the
    *next save* would then atomically replace the corrupt file with that empty
    view, destroying the operator's data forever. Renaming it first preserves
    the original bytes for recovery. Best-effort: returns the quarantine path,
    or None if the move failed (callers log either way and continue).
    """
    path = Path(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, target)
        return target
    except OSError:
        return None


def harden_directory_acl(
    path: str | Path, *, runner=subprocess.run
) -> tuple[bool, str]:
    """Restrict a directory's Windows ACL to SYSTEM, Administrators and the
    current user (inheritance disabled; new grants are inheritable).

    The 0600 mode bits set elsewhere in this module are a no-op on NTFS, so on a
    shared Windows machine this is what actually protects config.toml, .env,
    data/access.json and whatsmeow.db. Skipped (not failed) off Windows.
    Best-effort: returns (ok, message) and never raises; ``runner`` is injectable
    for tests.
    """
    if not sys.platform.startswith("win"):
        return (False, "skipped: ACL hardening is Windows-only")
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no resolvable user -> grant SIDs only
        user = ""
    grantees = [_SYSTEM_SID, _ADMINISTRATORS_SID] + ([user] if user else [])
    cmd = ["icacls", str(path), "/inheritance:r"]
    for grantee in grantees:
        # (OI)(CI)F = full control, inherited by existing and future children.
        cmd += ["/grant:r", f"{grantee}:(OI)(CI)F"]
    try:
        result = runner(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return (False, "icacls not found on PATH")
    except Exception as exc:  # noqa: BLE001 - hardening must never block setup
        return (False, f"icacls failed: {exc}")
    if result.returncode == 0:
        return (True, f"restricted ACL on {path} (SYSTEM, Administrators, {user or 'n/a'})")
    detail = (result.stderr or result.stdout or "").strip()
    return (False, f"icacls exited {result.returncode}: {detail}")
