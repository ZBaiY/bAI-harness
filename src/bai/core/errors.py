from __future__ import annotations


class BaiUserError(Exception):
    """Base class for deterministic user-facing boundary failures."""


class WorkspaceConfigError(BaiUserError):
    """Workspace configuration is missing, invalid, or unsafe."""


class HarnessBoundaryError(BaiUserError):
    """A requested Harness effect is outside the phase-one boundary."""
