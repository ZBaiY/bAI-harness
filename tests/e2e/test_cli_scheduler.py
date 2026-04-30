from __future__ import annotations

import json
from pathlib import Path

import pytest

import bai.cli
from bai.core.runtime import RuntimePaths
from conftest import load_json_output, run_cli


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def add_workspace(capsys: pytest.CaptureFixture[str], workspace_root: Path) -> dict:
    code, output, error = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert code == 0
    assert error == ""
    return load_json_output(output)


def test_cli_background_run_records_deferred_scheduler_metadata_without_execution(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], "--background", "dev plan only"],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    scheduler = read_json(result["scheduler_artifact"])
    assert result["status"] == "deferred"
    assert result["background"] is True
    assert scheduler["priority"] == "background"
    assert scheduler["lifecycle_status"] == "deferred"
    assert scheduler["execution_state"] == "deferred_metadata_only"
    assert "executed" not in scheduler
    assert scheduler["preemptible"] is True
    assert Path(result["scheduler_artifact"]).is_relative_to(runtime.state / "scheduler")
    assert not (runtime.memory / workspace["workspace_id"]).exists()
    assert not (runtime.state / "workflows").exists()
    assert not (runtime.state / "events").exists()


def test_cli_foreground_run_records_priority_over_deferred_background(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], "--background", "dev plan only"],
    )
    assert code == 0
    assert error == ""
    background_result = load_json_output(output)
    background = read_json(background_result["scheduler_artifact"])

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], "dev plan only"],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    foreground = read_json(result["scheduler_artifact"])
    background_after = read_json(background_result["scheduler_artifact"])
    assert result["status"] == "success"
    assert result["execution_policy"] == "serial"
    assert foreground["priority"] == "foreground"
    assert foreground["lifecycle_status"] == "completed"
    assert foreground["execution_state"] == "foreground_run_completed"
    assert foreground["harness_task_id"] == result["task_id"]
    assert foreground["run_status"] == result["status"]
    assert "executed" not in foreground
    assert foreground["preempts_background"] is True
    assert foreground["preempted_background_task_ids"] == [background["task_id"]]
    assert background_after["lifecycle_status"] == "preemptible_deferred"
    assert result["memory_artifacts"]


def test_cli_failed_foreground_run_updates_scheduler_admission(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], "inspect missing.txt"],
    )

    assert code == 2
    assert output == ""
    assert "inspect target does not exist" in error
    [scheduler_path] = sorted((runtime.state / "scheduler").glob("*.json"))
    scheduler = read_json(scheduler_path)
    assert scheduler["priority"] == "foreground"
    assert scheduler["lifecycle_status"] == "failed"
    assert scheduler["execution_state"] == "foreground_run_failed"
    assert "failure_reason" in scheduler
    assert scheduler["failure_reason"].startswith("inspect target does not exist")


def test_cli_internal_foreground_failure_updates_scheduler_then_returns_internal_error(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = add_workspace(capsys, workspace_root)

    class ExplodingHarness:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self, *args, **kwargs) -> dict:
            raise AssertionError("internal invariant exploded")

    monkeypatch.setattr(bai.cli, "Harness", ExplodingHarness)

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], "plan only"],
    )

    assert code == 1
    assert output == ""
    assert "bai internal error: AssertionError" in error
    [scheduler_path] = sorted((runtime.state / "scheduler").glob("*.json"))
    scheduler = read_json(scheduler_path)
    assert scheduler["lifecycle_status"] == "failed"
    assert scheduler["execution_state"] == "foreground_run_failed"
    assert scheduler["failure_reason"] == "internal error: AssertionError"
