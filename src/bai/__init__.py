"""Minimal bAI phase-one runtime."""

from __future__ import annotations

import importlib
import sys

__all__ = ["__version__"]

__version__ = "0.1.0"

_LEGACY_MODULES = {
    "agent": "bai.execution.agent",
    "approvals": "bai.artifacts.approvals",
    "context": "bai.artifacts.context",
    "effects": "bai.execution.effects",
    "errors": "bai.core.errors",
    "harness": "bai.execution.harness",
    "io": "bai.core.io",
    "memory": "bai.artifacts.memory",
    "policy": "bai.core.policy",
    "router": "bai.config.router",
    "runtime": "bai.core.runtime",
    "scheduler": "bai.execution.scheduler",
    "task_events": "bai.artifacts.task_events",
    "test_command": "bai.execution.test_command",
    "workflow": "bai.artifacts.workflow",
    "workspace": "bai.config.workspace",
}

for _legacy_name, _target in _LEGACY_MODULES.items():
    sys.modules.setdefault(f"{__name__}.{_legacy_name}", importlib.import_module(_target))
