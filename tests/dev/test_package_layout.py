from __future__ import annotations

import importlib


def test_legacy_module_imports_resolve_to_organized_packages() -> None:
    runtime = importlib.import_module("bai.runtime")
    harness = importlib.import_module("bai.harness")
    workspace = importlib.import_module("bai.workspace")

    assert runtime.RuntimePaths.__module__ == "bai.core.runtime"
    assert harness.Harness.__module__ == "bai.execution.harness"
    assert workspace.WorkspaceStore.__module__ == "bai.config.workspace"
