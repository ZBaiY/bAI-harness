from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.core.runtime import RuntimePaths
from conftest import assert_not_under, assert_under, load_json_output, run_cli


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def add_workspace(capsys: pytest.CaptureFixture[str], workspace_root: Path) -> dict:
    code, output, error = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert code == 0
    assert error == ""
    return load_json_output(output)


def assert_runtime_artifact(path: str, runtime: RuntimePaths, workspace_root: Path) -> None:
    assert Path(path).exists()
    assert_under(path, runtime.home)
    assert_not_under(path, workspace_root)


def test_complete_plan_run_exposes_phase_one_artifacts(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    before = {path.name for path in workspace_root.iterdir()}

    code, output, error = run_cli(
        capsys, ["run", "--workspace", workspace["workspace_id"], "plan only"]
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    assert result["status"] == "success"
    for key in [
        "task_id",
        "execution_policy",
        "workspace_id",
        "model",
        "plan",
        "workflow_artifact",
        "context_artifact",
        "approval_artifacts",
        "event_artifacts",
        "memory_artifacts",
        "inspections",
        "applied_changes",
    ]:
        assert key in result
    assert result["approval_artifact"] in result["approval_artifacts"]
    assert result["memory_artifact"] in result["memory_artifacts"]
    assert result["working_memory_artifact"] in result["memory_artifacts"]
    assert result["execution_policy"] == "serial"
    assert result["inspections"] == []
    assert result["applied_changes"] == []
    assert {path.name for path in workspace_root.iterdir()} == before

    artifacts = [
        result["workflow_artifact"],
        result["context_artifact"],
        *result["approval_artifacts"],
        *result["event_artifacts"],
        *result["memory_artifacts"],
    ]
    for artifact in artifacts:
        assert_runtime_artifact(artifact, runtime, workspace_root)

    workflow = read_json(result["workflow_artifact"])
    context = read_json(result["context_artifact"])
    event_types = [read_json(path)["event_type"] for path in result["event_artifacts"]]
    session_memory = read_json(result["memory_artifact"])
    working_memory = read_json(result["working_memory_artifact"])

    assert event_types == ["started", "completed"]
    assert workflow["task_id"] == result["task_id"]
    assert workflow["workspace_id"] == workspace["workspace_id"]
    assert workflow["execution_policy"] == "serial"
    assert workflow["status"] == "completed"
    assert workflow["artifacts"]["event_artifacts"] == result["event_artifacts"]
    assert [node["id"] for node in workflow["nodes"]] == ["plan", "memory_write"]
    assert context["task_id"] == result["task_id"]
    assert context["user_request"] == "plan only"
    assert context["router_constraints"] == {"local": True, "workspace_network": False}
    assert session_memory["kind"] == "session"
    assert working_memory["kind"] == "working"


def test_complete_inspect_run_records_inspect_workflow_node(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)

    code, output, error = run_cli(
        capsys, ["run", "--workspace", workspace["workspace_id"], "inspect README.md"]
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    workflow = read_json(result["workflow_artifact"])

    assert result["inspections"][0]["path"] == str((workspace_root / "README.md").resolve())
    assert [node["kind"] for node in workflow["nodes"]] == [
        "plan",
        "inspect",
        "memory_write",
    ]
    inspect_node = [node for node in workflow["nodes"] if node["kind"] == "inspect"][0]
    assert inspect_node["can_mutate"] is False
    assert inspect_node["effect"] == {
        "kind": "inspect_file",
        "path": str((workspace_root / "README.md").resolve()),
    }
    assert_under(result["workflow_artifact"], runtime.state / "workflows")
    assert_under(result["context_artifact"], runtime.state / "context")


def test_complete_approved_mutation_run_records_mutation_workflow_node(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    target = workspace_root / "created.md"

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(target),
            f"propose-create {target}",
        ],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    workflow = read_json(result["workflow_artifact"])
    mutation_approvals = [
        path
        for path in result["approval_artifacts"]
        if read_json(path)["kind"] == "mutation_approval"
    ]

    assert target.exists()
    assert len(mutation_approvals) == 1
    assert [node["kind"] for node in workflow["nodes"]] == [
        "plan",
        "code_mutation",
        "memory_write",
    ]
    mutation_node = [node for node in workflow["nodes"] if node["kind"] == "code_mutation"][0]
    assert mutation_node["can_mutate"] is True
    assert mutation_node["approval_artifact"] == mutation_approvals[0]
    assert mutation_node["effect"] == {
        "kind": "code_mutation",
        "path": str(target.resolve()),
        "operation": "create",
    }


def test_failed_inspect_keeps_started_workflow_context_and_failed_event(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)

    code, output, error = run_cli(
        capsys, ["run", "--workspace", workspace["workspace_id"], "inspect missing.txt"]
    )

    assert code == 2
    assert output == ""
    assert "inspect target does not exist" in error
    workflow_files = sorted((runtime.state / "workflows").glob("*.json"))
    context_files = sorted((runtime.state / "context").glob("*.json"))
    event_files = sorted((runtime.state / "events").glob("*/*.json"))
    assert len(workflow_files) == 1
    assert len(context_files) == 1
    assert [read_json(path)["event_type"] for path in event_files] == ["started", "failed"]
    workflow = read_json(workflow_files[0])
    assert workflow["status"] == "failed"
    assert workflow["artifacts"]["event_artifacts"] == [
        str(path) for path in event_files
    ]
    assert workflow["artifacts"]["memory_artifacts"] == []
    assert [node["kind"] for node in workflow["nodes"]] == ["plan", "inspect", "memory_write"]
    assert not (runtime.memory / workspace["workspace_id"]).exists()
    assert_runtime_artifact(str(workflow_files[0]), runtime, workspace_root)
    assert_runtime_artifact(str(context_files[0]), runtime, workspace_root)
