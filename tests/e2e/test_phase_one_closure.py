from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.core.runtime import RuntimePaths
from conftest import load_json_output, run_cli


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def add_workspace(capsys: pytest.CaptureFixture[str], workspace_root: Path) -> dict:
    code, output, error = run_cli(
        capsys,
        ["workspace", "add", "demo", str(workspace_root)],
    )
    assert code == 0
    assert error == ""
    return load_json_output(output)


def assert_artifact_under_bai_home(path: str | Path, runtime: RuntimePaths) -> None:
    resolved = Path(path).resolve()
    assert resolved.is_relative_to(runtime.home)


def test_phase_one_runtime_artifacts_stay_under_bai_home_not_source_or_workspace(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root = Path.cwd().resolve()
    workspace = add_workspace(capsys, workspace_root)
    target = workspace_root / "needs-approval.txt"

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"dev propose-create {target.name}",
        ],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    artifact_fields = [
        "workflow_artifact",
        "context_artifact",
        "approval_artifact",
        "audit_artifact",
        "fix_artifact",
        "scheduler_artifact",
        "memory_artifact",
        "working_memory_artifact",
    ]
    for field in artifact_fields:
        assert result[field], field
        assert_artifact_under_bai_home(result[field], runtime)
        assert not Path(result[field]).resolve().is_relative_to(source_root)
        assert not Path(result[field]).resolve().is_relative_to(workspace_root)
    for path in [
        *result["approval_artifacts"],
        *result["event_artifacts"],
        *result["memory_artifacts"],
    ]:
        assert_artifact_under_bai_home(path, runtime)
        assert not Path(path).resolve().is_relative_to(source_root)
        assert not Path(path).resolve().is_relative_to(workspace_root)

    assert (runtime.config / "workspaces" / f"{workspace['workspace_id']}.json").exists()
    assert read_json(result["workflow_artifact"])["artifacts"]["fix_artifact"] == result["fix_artifact"]
    assert read_json(result["scheduler_artifact"])["lifecycle_status"] == "completed"
    assert not target.exists()
    assert not (workspace_root / ".bai").exists()
    assert not (source_root / ".bai").exists()


def test_background_run_writes_only_scheduler_metadata_not_harness_artifacts(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--background",
            "dev plan only",
        ],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    scheduler = read_json(result["scheduler_artifact"])
    assert result == {
        "status": "deferred",
        "background": True,
        "workspace_id": workspace["workspace_id"],
        "scheduler_artifact": result["scheduler_artifact"],
    }
    assert scheduler["lifecycle_status"] == "deferred"
    assert scheduler["execution_state"] == "deferred_metadata_only"
    assert scheduler["preemptible"] is True
    assert "harness_task_id" not in scheduler
    assert "run_status" not in scheduler
    assert Path(result["scheduler_artifact"]).is_relative_to(runtime.state / "scheduler")
    assert not (runtime.state / "events").exists()
    assert not (runtime.state / "workflows").exists()
    assert not (runtime.state / "context").exists()
    assert not (runtime.state / "approvals").exists()
    assert not (runtime.state / "audits").exists()
    assert not (runtime.state / "fixes").exists()
    assert not (runtime.memory / workspace["workspace_id"]).exists()
