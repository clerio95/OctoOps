"""One-time, expiring invites for onboarding brand-new users by interface.

An admin creates an invite for a role; the bot returns a ``t.me/<bot>?start=<nonce>``
deep link. When the (not-yet-whitelisted) invitee taps it and presses Start, the
transport gate redeems the nonce and grants them that role — see the gate in
``transports/telegram/adapter.py``. This is the *only* path by which the bot
responds to a non-whitelisted user: a valid one-time nonce. Randoms with no nonce
(or a wrong/expired one) still get silence, so the bot isn't revealed to spam.

Invites are single-use (redeem removes them), time-boxed (default 24h), and
persisted to ``data/invites.json`` so a restart between creation and redemption
doesn't break the link. The clock is injectable for testing expiry.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from octoops.core.logging import get_logger
from octoops.core.secure_io import quarantine_corrupt, write_private_text
from octoops.shared.models import Role

_log = get_logger("octoops.core.invites")

_DEFAULT_TTL = 24 * 3600  # seconds


@dataclass
class Invite:
    nonce: str
    role: Role
    expires_at: float  # epoch seconds

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


class InviteStore:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._ttl = ttl_seconds
        self._invites: dict[str, Invite] = self._load()

    # --- operations -----------------------------------------------------------

    def create(self, role: Role) -> Invite:
        """Mint a single-use invite for a role and persist it."""
        self._prune()
        invite = Invite(
            nonce=secrets.token_urlsafe(9),
            role=role,
            expires_at=self._clock() + self._ttl,
        )
        self._invites[invite.nonce] = invite
        self._save()
        return invite

    def redeem(self, nonce: str) -> Invite | None:
        """Consume an invite by nonce. Returns it, or None if unknown/expired."""
        self._prune()
        invite = self._invites.pop(nonce, None)
        if invite is None:
            return None
        self._save()
        return invite

    def pending(self) -> list[Invite]:
        """Non-expired invites, soonest-to-expire first."""
        self._prune()
        return sorted(self._invites.values(), key=lambda i: i.expires_at)

    # --- internals ------------------------------------------------------------

    def _prune(self) -> None:
        now = self._clock()
        expired = [n for n, inv in self._invites.items() if inv.is_expired(now)]
        for nonce in expired:
            del self._invites[nonce]
        if expired:
            self._save()

    def _load(self) -> dict[str, Invite]:
        if self._path is None or not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            out: dict[str, Invite] = {}
            for item in raw.get("invites", []):
                out[item["nonce"]] = Invite(
                    nonce=item["nonce"],
                    role=Role.from_str(item["role"]),
                    expires_at=float(item["expires_at"]),
                )
            return out
        except OSError as exc:
            # Couldn't read — the file itself may be fine, so leave it in place.
            _log.error("invites.load_failed", path=str(self._path), error=str(exc))
            return {}
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            # Unparseable content: move it aside so the next _save() can't replace
            # the pending invites with this empty view.
            quarantined = quarantine_corrupt(self._path)
            _log.error(
                "invites.corrupt_quarantined",
                path=str(self._path),
                quarantined=str(quarantined),
                error=str(exc),
            )
            return {}

    def _save(self) -> None:
        if self._path is None:
            return
        data = {
            "invites": [
                {"nonce": inv.nonce, "role": inv.role.name.lower(), "expires_at": inv.expires_at}
                for inv in self._invites.values()
            ]
        }
        try:
            # 0600: nonces here grant a role on redemption — keep them private.
            write_private_text(self._path, json.dumps(data, indent=2))
        except OSError as exc:
            _log.error("invites.save_failed", path=str(self._path), error=str(exc))
