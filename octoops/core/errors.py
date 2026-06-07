"""OctoOpsError hierarchy.

Handlers, jobs, and listeners must not raise to the runtime — these are for
core-level failures (config, auth, transport, module loading).
"""

from __future__ import annotations


class OctoOpsError(Exception):
    """Base class for all OctoOps errors."""


class ConfigError(OctoOpsError):
    """Missing or invalid configuration. Fatal at startup."""


class AuthError(OctoOpsError):
    """Authorization could not be resolved or was denied."""


class TransportError(OctoOpsError):
    """A transport failed to start, send, or communicate with its backend."""


class ModuleLoadError(OctoOpsError):
    """A module's manifest, import, or load() call failed."""


class RouterError(OctoOpsError):
    """Invalid router state, e.g. a duplicate command registration."""


class PermissionsError(OctoOpsError):
    """An invalid runtime permission change, e.g. revoking the last admin."""
