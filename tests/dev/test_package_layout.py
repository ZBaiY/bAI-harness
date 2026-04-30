from __future__ import annotations

import ast
import importlib
import tomllib
from pathlib import Path


def test_legacy_module_imports_resolve_to_organized_packages() -> None:
    runtime = importlib.import_module("bai.runtime")
    harness = importlib.import_module("bai.harness")
    workspace = importlib.import_module("bai.workspace")

    assert runtime.RuntimePaths.__module__ == "bai.core.runtime"
    assert harness.Harness.__module__ == "bai.execution.harness"
    assert workspace.WorkspaceStore.__module__ == "bai.config.workspace"


def test_phase_one_has_no_forbidden_runtime_dependencies_or_imports() -> None:
    forbidden = {
        "apscheduler",
        "celery",
        "chromadb",
        "faiss",
        "langgraph",
        "litellm",
        "patchoptic",
        "whisper",
    }
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["dependencies"] == []
    for path in Path("src/bai").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported = alias.name.split(".", 1)[0].lower()
                    assert imported not in forbidden, f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = node.module.split(".", 1)[0].lower()
                assert imported not in forbidden, f"{path} imports {node.module}"
