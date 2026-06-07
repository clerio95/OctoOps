"""Role resolution and authorization. Fail closed.

Roles come from two layers:

1. The ``[core]`` ID lists in ``config.toml`` — the immutable base. Hand-edited,
   authoritative, never rewritten by the bot.
2. Runtime grants made via the ``access`` module (``/grant`` / ``/revoke``),
   persisted to ``data/access.json`` by an optional ``RoleStore`` and layered on
   top of the base.

A user's effective role is the **highest** role any layer assigns them; a user in
no layer has no role and is denied. Runtime grants can only *add* access — a
config-declared user can't be downgraded or removed at runtime (edit the config),
which also means you can't lock the config admins out from the interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from octoops.core.errors import PermissionsError
from octoops.shared.models import Role, UserId

if TYPE_CHECKING:
    from octoops.core.role_store import RoleStore

# Re-exported so modules/handlers can `from octoops.core.permissions import Role`.
__all__ = ["Role", "Permissions"]


class Permissions:
    def __init__(
        self,
        *,
        allowed_user_ids: list[str],
        operator_user_ids: list[str],
        admin_user_ids: list[str],
        default_role: Role = Role.Viewer,
        store: "RoleStore | None" = None,
        runtime_grants: dict[str, Role] | None = None,
    ) -> None:
        self._allowed = set(allowed_user_ids)
        self._operators = set(operator_user_ids)
        self._admins = set(admin_user_ids)
        self._default_role = default_role
        self._store = store
        self._runtime: dict[str, Role] = dict(runtime_grants or {})

    # --- resolution -----------------------------------------------------------

    def _in_config(self, uid: str) -> bool:
        return uid in self._allowed or uid in self._operators or uid in self._admins

    def role_for(self, user_id: UserId) -> Role | None:
        """Resolve the highest role for a user across all layers, or None (deny)."""
        uid = str(user_id)
        candidates: list[Role] = []
        if uid in self._admins:
            candidates.append(Role.Admin)
        if uid in self._operators:
            candidates.append(Role.Operator)
        if uid in self._allowed:
            candidates.append(self._default_role)
        if uid in self._runtime:
            candidates.append(self._runtime[uid])
        if not candidates:
            return None
        return max(candidates)

    def authorize(self, user_id: UserId, min_role: Role) -> bool:
        """True iff the user's resolved role meets or exceeds min_role."""
        role = self.role_for(user_id)
        return role is not None and role >= min_role

    # --- runtime management (Option A: config base immutable, grants persisted)

    def grant(self, user_id: UserId, role: Role) -> None:
        """Add or replace a runtime grant for a user, then persist."""
        self._runtime[str(user_id)] = role
        self._persist()

    def revoke(self, user_id: UserId) -> None:
        """Remove a user's runtime grant, then persist.

        Raises PermissionsError if the user has no runtime grant (e.g. they're
        defined in config.toml — edit it to change them) or if removing them
        would leave no admin at all.
        """
        uid = str(user_id)
        if uid not in self._runtime:
            if self._in_config(uid):
                raise PermissionsError(
                    f"{uid} is defined in config.toml — edit it to change them"
                )
            raise PermissionsError(f"{uid} has no runtime grant to revoke")
        if self._runtime[uid] is Role.Admin and len(self._effective_admins()) <= 1:
            raise PermissionsError("refusing to revoke the last remaining admin")
        del self._runtime[uid]
        self._persist()

    def _effective_admins(self) -> set[str]:
        return set(self._admins) | {
            uid for uid, role in self._runtime.items() if role is Role.Admin
        }

    def known_users(self) -> dict[str, Role]:
        """Every user with any role, mapped to their effective (highest) role."""
        uids = self._allowed | self._operators | self._admins | set(self._runtime)
        return {uid: role for uid in uids if (role := self.role_for(uid)) is not None}

    def is_runtime_only(self, user_id: UserId) -> bool:
        """True if the user exists only as a runtime grant (not in config)."""
        uid = str(user_id)
        return uid in self._runtime and not self._in_config(uid)

    def _persist(self) -> None:
        if self._store is not None:
            self._store.save(self._runtime)
