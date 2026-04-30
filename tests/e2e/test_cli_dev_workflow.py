from __future__ import annotations

import json
import shlex
import sys
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


def allow_test(
    capsys: pytest.CaptureFixture[str], workspace_id: str, argv: list[str]
) -> dict:
    code, output, error = run_cli(
        capsys,
        ["workspace", "allow-test", workspace_id, "--argv", json.dumps(argv)],
    )
    assert code == 0
    assert error == ""
    return load_json_output(output)


def test_cli_dev_workflow_happy_path_artifacts_without_workspace_mutation(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    before = {path.name for path in workspace_root.iterdir()}

    code, output, error = run_cli(
        capsys, ["run", "--workspace", workspace["workspace_id"], "dev plan only"]
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    workflow = read_json(result["workflow_artifact"])
    event_types = [read_json(path)["event_type"] for path in result["event_artifacts"]]
    audit = read_json(result["audit_artifact"])
    assert result["workflow_mode"] == "dev"
    assert result["node_results"]["code"]["status"] == "skipped"
    assert result["audit_findings"] == [
        {
            "severity": "info",
            "code": "no_test_command",
            "message": "no trusted test command ran",
        }
    ]
    assert audit["findings"] == result["audit_findings"]
    assert result["fix_artifact"] is None
    assert result["fix_proposals"] == []
    assert workflow["artifacts"]["audit_artifact"] == result["audit_artifact"]
    assert [node["id"] for node in workflow["nodes"]] == [
        "plan",
        "code",
        "doc",
        "test",
        "audit",
        "fix",
    ]
    assert event_types == ["started", "completed"]
    for artifact in [
        result["workflow_artifact"],
        result["context_artifact"],
        result["audit_artifact"],
        *result["approval_artifacts"],
        *result["event_artifacts"],
        *result["memory_artifacts"],
    ]:
        assert_under(artifact, runtime.home)
        assert_not_under(artifact, workspace_root)
    assert {path.name for path in workspace_root.iterdir()} == before


def test_cli_dev_workflow_approved_mutation_and_test_command(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    target = workspace_root / "created.md"
    argv = [
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('created.md').exists(); print('ok')",
    ]
    allow_test(capsys, workspace["workspace_id"], argv)

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(target),
            f"dev propose-create {target} test {shlex.join(argv)}",
        ],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    workflow = read_json(result["workflow_artifact"])
    approval_kinds = [read_json(path)["kind"] for path in result["approval_artifacts"]]
    assert target.exists()
    assert approval_kinds == ["agent_output_acceptance", "mutation_approval"]
    assert result["test_runs"][0]["stdout_preview"] == "ok\n"
    assert result["node_results"]["code"]["status"] == "completed"
    assert result["node_results"]["test"]["status"] == "completed"
    assert result["node_results"]["audit"]["status"] == "passed_with_notes"
    assert result["node_results"]["fix"]["status"] == "skipped"
    assert result["audit_findings"] == [
        {
            "severity": "info",
            "code": "trusted_test_command_no_sandbox",
            "message": "test command ran as trusted direct execution without a write sandbox",
        }
    ]
    assert workflow["artifacts"]["node_results"] == result["node_results"]


def test_cli_dev_workflow_missing_mutation_approval_writes_audit_warning(
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
            f"dev propose-create {target}",
        ],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    audit = read_json(result["audit_artifact"])
    assert result["status"] == "completed_with_denials"
    assert not target.exists()
    assert audit["status"] == "warnings"
    assert result["fix_artifact"] is not None
    fix = read_json(result["fix_artifact"])
    assert fix["source_audit_artifact"] == result["audit_artifact"]
    assert any(
        finding["code"] == "mutation_denied"
        and finding["path"] == str(target.resolve())
        for finding in audit["findings"]
    )
    assert result["fix_proposals"] == [
        {
            "status": "proposal_only",
            "kind": "request_mutation_approval",
            "message": "request approval for denied mutation path",
            "path": str(target.resolve()),
            "operation": "create",
            "source_finding_code": "mutation_denied",
            "auto_apply": False,
        }
    ]


def test_cli_dev_workflow_failed_test_has_failed_event_no_memory_or_fix(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    argv = [sys.executable, "-c", "import sys; sys.exit(6)"]
    allow_test(capsys, workspace["workspace_id"], argv)

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], f"dev test {shlex.join(argv)}"],
    )

    assert code == 2
    assert output == ""
    assert "test command failed with exit code 6" in error
    event_types = [
        read_json(path)["event_type"]
        for path in sorted((runtime.state / "events").glob("*/*.json"))
    ]
    workflow = read_json(next((runtime.state / "workflows").glob("*.json")))
    assert event_types == ["started", "failed"]
    assert workflow["status"] == "failed"
    assert workflow["artifacts"]["node_results"]["test"]["status"] == "failed"
    assert workflow["artifacts"]["node_results"]["audit"]["status"] == "failed"
    assert workflow["artifacts"]["node_results"]["fix"]["status"] == "proposal_only"
    audit = read_json(workflow["artifacts"]["audit_artifact"])
    fix = read_json(workflow["artifacts"]["fix_artifact"])
    assert audit["status"] == "failed"
    assert audit["findings"] == workflow["artifacts"]["audit_findings"]
    assert fix["proposals"] == workflow["artifacts"]["fix_proposals"]
    assert not (runtime.memory / workspace["workspace_id"]).exists()
