from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import bai.cli
from bai.core.runtime import RuntimePaths
from conftest import load_json_output, run_cli


def test_console_script_entrypoint_is_declared() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["bai"] == "bai.cli:main"


def test_module_entrypoint_subprocess_smoke(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["BAI_HOME"] = str(tmp_path / ".bai")
    env["PYTHONPATH"] = str(Path("src").resolve())

    result = subprocess.run(
        [sys.executable, "-m", "bai.cli", "--help"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )

    assert result.returncode == 0
    assert "usage: bai" in result.stdout
    assert result.stderr == ""


def test_bare_cli_without_workspace_fails_with_guidance(
    capsys,
) -> None:
    code, output, error = run_cli(capsys, ["plan only"])

    assert code == 2
    assert output == ""
    assert "--workspace <name-or-id>" in error


def test_bare_cli_routes_request_through_harness(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys,
) -> None:
    add_code, add_output, _ = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    workspace = load_json_output(add_output)

    run_code, run_output, run_error = run_cli(
        capsys, ["--workspace", workspace["workspace_id"], "plan only"]
    )

    assert run_code == 0
    assert run_error == ""
    result = load_json_output(run_output)
    assert result["execution_policy"] == "serial"
    assert result["workspace_id"] == workspace["workspace_id"]
    assert result["model"]["provider"] == "local"
    assert Path(result["approval_artifact"]).is_relative_to(runtime.state)
    assert Path(result["memory_artifact"]).is_relative_to(runtime.memory)


def test_cli_internal_error_uses_internal_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    def explode(*args, **kwargs) -> int:
        raise AssertionError("invariant exploded")

    monkeypatch.setattr(bai.cli, "run_harness", explode)

    code, output, error = run_cli(capsys, ["run", "--workspace", "demo", "plan only"])

    assert code == 1
    assert output == ""
    assert "bai internal error: AssertionError" in error


def test_cli_os_error_uses_internal_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    def explode(*args, **kwargs) -> int:
        raise OSError("runtime state unavailable")

    monkeypatch.setattr(bai.cli, "run_harness", explode)

    code, output, error = run_cli(capsys, ["run", "--workspace", "demo", "plan only"])

    assert code == 1
    assert output == ""
    assert "bai internal error: OSError" in error


def test_cli_unclassified_value_error_uses_internal_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    def explode(*args, **kwargs) -> int:
        raise ValueError("internal invariant")

    monkeypatch.setattr(bai.cli, "run_harness", explode)

    code, output, error = run_cli(capsys, ["run", "--workspace", "demo", "plan only"])

    assert code == 1
    assert output == ""
    assert "bai internal error: ValueError" in error
