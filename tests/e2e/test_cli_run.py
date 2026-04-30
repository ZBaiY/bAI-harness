from __future__ import annotations

from pathlib import Path

import pytest

from bai.core.runtime import RuntimePaths

from conftest import assert_under, load_json_output, run_cli


def read_event(path: Path) -> dict:
    return load_json_output(path.read_text())


def read_json_file(path: Path) -> dict:
    return load_json_output(path.read_text())


def test_cli_run_lifecycle_records_runtime_artifacts_without_mutating_workspace(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_code, add_output, _ = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    workspace = load_json_output(add_output)

    before = {path.name for path in workspace_root.iterdir()}
    run_code, run_output, run_error = run_cli(
        capsys, ["run", "--workspace", workspace["name"], "plan only"]
    )

    assert run_code == 0
    assert run_error == ""
    result = load_json_output(run_output)
    assert result["execution_policy"] == "serial"
    assert result["model"] == {
        "provider": "local",
        "model": "local-plan-stub",
        "endpoint": "stub://local/plan",
        "network_required": False,
    }
    assert result["task_id"]
    assert len(result["event_artifacts"]) == 2
    assert result["approval_artifact"]
    assert result["approval_artifacts"] == [result["approval_artifact"]]
    approval = read_json_file(Path(result["approval_artifact"]))
    assert approval["kind"] == "agent_output_acceptance"
    assert approval["status"] == "accepted"
    assert approval["task_id"] == result["task_id"]
    event_paths = [Path(path) for path in result["event_artifacts"]]
    assert [read_event(path)["event_type"] for path in event_paths] == [
        "started",
        "completed",
    ]
    assert {read_event(path)["task_id"] for path in event_paths} == {result["task_id"]}
    for path in event_paths:
        assert_under(path, runtime.state / "events")
        assert path.exists()
    assert_under(result["approval_artifact"], runtime.state)
    assert_under(result["memory_artifact"], runtime.memory)
    assert Path(result["approval_artifact"]).exists()
    assert Path(result["memory_artifact"]).exists()
    assert {path.name for path in workspace_root.iterdir()} == before


def test_cli_mutation_requires_explicit_approval_and_workspace_scope(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_code, add_output, _ = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    workspace = load_json_output(add_output)
    inside_target = workspace_root / "created.md"
    outside_target = workspace_root.parent / "outside.md"

    unapproved_code, unapproved_output, unapproved_error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], f"propose-create {inside_target}"],
    )
    assert unapproved_code == 0
    assert unapproved_error == ""
    unapproved_result = load_json_output(unapproved_output)
    assert unapproved_result["status"] == "completed_with_denials"
    assert unapproved_result["applied_changes"] == []
    assert unapproved_result["denied_changes"] == [
        {
            "path": str(inside_target.resolve()),
            "operation": "create",
            "status": "denied",
            "reason": "mutation requires harness approval",
        }
    ]
    assert not inside_target.exists()

    approved_code, approved_output, approved_error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(inside_target),
            f"propose-create {inside_target}",
        ],
    )
    assert approved_code == 0
    assert approved_error == ""
    approved_result = load_json_output(approved_output)
    assert approved_result["applied_changes"] == [
        {"path": str(inside_target.resolve()), "operation": "create"}
    ]
    approval_kinds = [
        read_json_file(Path(path))["kind"]
        for path in approved_result["approval_artifacts"]
    ]
    assert approval_kinds == ["agent_output_acceptance", "mutation_approval"]
    assert inside_target.exists()

    outside_code, outside_output, outside_error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(outside_target),
            f"propose-create {outside_target}",
        ],
    )
    assert outside_code == 2
    assert outside_output == ""
    assert "outside allowed workspace paths" in outside_error
    assert not outside_target.exists()
    approvals = sorted((runtime.state / "approvals").glob("*.json"))
    assert [
        read_json_file(path)["kind"] for path in approvals
    ].count("mutation_approval") == 1


def test_cli_approved_create_rejects_existing_target_without_mutation_approval(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_code, add_output, _ = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    workspace = load_json_output(add_output)
    target = workspace_root / "created.md"
    target.write_text("existing content\n")

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

    assert code == 2
    assert output == ""
    assert "create target already exists" in error
    assert target.read_text() == "existing content\n"
    approvals = [read_json_file(path) for path in sorted((runtime.state / "approvals").glob("*.json"))]
    assert [approval["kind"] for approval in approvals] == ["agent_output_acceptance"]


def test_cli_approved_path_traversal_mutation_escape_is_rejected(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_code, add_output, _ = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    workspace = load_json_output(add_output)
    escaped = workspace_root / ".." / "escaped.md"

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(escaped),
            f"propose-create {escaped}",
        ],
    )

    assert code == 2
    assert output == ""
    assert "outside allowed workspace paths" in error
    assert not escaped.resolve().exists()


def test_cli_failed_run_records_failed_event_under_runtime_state(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_code, add_output, _ = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    workspace = load_json_output(add_output)

    code, output, error = run_cli(
        capsys, ["run", "--workspace", workspace["workspace_id"], "inspect missing.txt"]
    )

    assert code == 2
    assert output == ""
    assert "inspect target does not exist" in error
    event_files = sorted((runtime.state / "events").glob("*/*.json"))
    events = [read_event(path) for path in event_files]
    assert [event["event_type"] for event in events] == ["started", "failed"]
    assert len({event["task_id"] for event in events}) == 1
    for path in event_files:
        assert_under(path, runtime.state / "events")
    assert not (runtime.memory / workspace["workspace_id"]).exists()
