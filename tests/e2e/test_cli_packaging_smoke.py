from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from conftest import load_json_output


def run_declared_bai(
    *,
    argv: list[str],
    bai_home: Path,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    script = (
        "import importlib, sys; "
        "module_name, func_name = 'bai.cli:main'.split(':'); "
        "func = getattr(importlib.import_module(module_name), func_name); "
        "raise SystemExit(func(sys.argv[1:]))"
    )
    env = os.environ.copy()
    env["BAI_HOME"] = str(bai_home)
    env["PYTHONPATH"] = str((cwd / "src").resolve())
    return subprocess.run(
        [sys.executable, "-c", script, *argv],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def test_pyproject_metadata_stays_minimal() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["bai"] == "bai.cli:main"
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]


def test_declared_console_script_target_help_subprocess_smoke(tmp_path: Path) -> None:
    result = run_declared_bai(
        argv=["--help"],
        bai_home=tmp_path / ".bai",
        cwd=Path.cwd(),
    )

    assert result.returncode == 0
    assert "usage: bai" in result.stdout
    assert result.stderr == ""


def test_declared_console_script_target_workspace_lifecycle_subprocess(
    tmp_path: Path,
) -> None:
    bai_home = tmp_path / ".bai"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# workspace\n")
    cwd = Path.cwd()

    add = run_declared_bai(
        argv=["workspace", "add", "demo", str(workspace)],
        bai_home=bai_home,
        cwd=cwd,
    )
    assert add.returncode == 0
    added = load_json_output(add.stdout)
    assert added["name"] == "demo"
    assert added["root_path"] == str(workspace.resolve())

    listed = run_declared_bai(
        argv=["workspace", "list"],
        bai_home=bai_home,
        cwd=cwd,
    )
    assert listed.returncode == 0
    assert load_json_output(listed.stdout)[0]["workspace_id"] == added["workspace_id"]

    shown = run_declared_bai(
        argv=["workspace", "show", added["workspace_id"]],
        bai_home=bai_home,
        cwd=cwd,
    )
    assert shown.returncode == 0
    assert load_json_output(shown.stdout) == added
    assert (bai_home / "config" / "workspaces" / f"{added['workspace_id']}.json").exists()
    assert not (workspace / ".bai").exists()


def test_generated_artifact_patterns_are_gitignored_or_absent() -> None:
    root = Path.cwd()
    gitignore = root / ".gitignore"
    ignored_patterns = set(gitignore.read_text().splitlines()) if gitignore.exists() else set()

    for pattern in ["__pycache__/", ".pytest_cache/", "build/", "dist/", "*.egg-info/"]:
        assert pattern in ignored_patterns

    assert not (root / "build").exists()
    assert not (root / "dist").exists()
    assert not list(root.glob("*.egg-info"))
