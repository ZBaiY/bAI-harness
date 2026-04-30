"""Shared exception types for user-facing and harness-boundary failures.

The CLI uses these classes to separate expected boundary failures from internal
invariants. Internal bugs should still surface as internal errors instead of
being normalized into ordinary user mistakes.
"""

from __future__ import annotations


class BaiUserError(Exception):
    """Base class for deterministic user-facing boundary failures."""


class WorkspaceConfigError(BaiUserError):
    """Workspace configuration is missing, invalid, or unsafe."""


class HarnessBoundaryError(BaiUserError):
    """A requested Harness effect is outside the phase-one boundary."""
