"""Single-instance lock so two OctoOps processes can't fight over the bot.

Two live instances poll the same Telegram token (alternating getUpdates 409
conflicts) and race for the bridge/callback ports. The classic trap: an operator
starts a console run to debug while the Task Scheduler instance is still alive,
and both behave insanely.

The lock is an OS-level file lock (fcntl.flock on POSIX, msvcrt.locking on
Windows), which the kernel releases automatically when the process dies — so a
crash can never leave a stale lock behind. The holder's pid and start time are
written into the file purely for the "already running" message shown to the
second instance. On Windows the locked byte sits far past the data region so
other processes can still read that info while the lock is held.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

# Windows byte-range locks are mandatory: lock a byte far beyond any data we
# write, so a second process can still read the holder info at offset 0.
_LOCK_OFFSET = 1 << 30


class InstanceLock:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._fh = None

    def acquire(self) -> bool:
        """Take the lock. False if another live process holds it."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(_LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        # We hold the lock; record who we are for the second instance's message.
        fh.seek(0)
        fh.truncate()
        fh.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started": datetime.now().isoformat(timespec="seconds"),
                }
            )
        )
        fh.flush()
        self._fh = fh
        return True

    def holder(self) -> dict:
        """Best-effort info about the current holder (for error messages)."""
        try:
            return json.loads(self._path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(_LOCK_OFFSET)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None
        # The file is left in place: unlinking on POSIX can race a concurrent
        # acquire (which would then hold a lock on a deleted inode). Harmless.
