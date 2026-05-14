from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from bai.core.runtime import RuntimePaths
from bai.optic_category import (
    OpticCategoryError,
    build_bug_fix_structural_loop_registry,
)
from conftest import assert_not_under, assert_under, load_json_output


REPO_ROOT = Path(__file__).resolve().parents[2]
DECLARED_CLI_TARGET = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())[
    "project"
]["scripts"]["bai"]
DEV_NODE_IDS = ["plan", "code", "doc", "test", "audit", "fix"]
BUGFIX_OPTIC_IDS = [
    "RoadmapExtractorOptic",
    "AuditorOptic",
    "PlannerOptic",
    "PatcherOptic",
    "ReviewerOptic",
]
BUGFIX_HANDOFFS = [
    ("RoadmapExtractorOptic", "RepositoryMapView", "AuditorOptic", "RepositoryMapView"),
    ("RoadmapExtractorOptic", "RepositoryMapView", "PlannerOptic", "RepositoryMapView"),
    ("AuditorOptic", "AuditReport", "PlannerOptic", "AuditReport"),
    ("AuditorOptic", "AuditReport", "PatcherOptic", "AuditReport"),
    ("PlannerOptic", "RepairPlan", "PatcherOptic", "RepairPlan"),
    ("RoadmapExtractorOptic", "RepositoryMapView", "ReviewerOptic", "RepositoryMapView"),
    ("AuditorOptic", "AuditReport", "ReviewerOptic", "AuditReport"),
    ("PlannerOptic", "RepairPlan", "ReviewerOptic", "RepairPlan"),
    ("PatcherOptic", "PatchDiff", "ReviewerOptic", "PatchDiff"),
]
BUGFIX_ROLE_TRACE_EXPECTATIONS = {
    "RoadmapExtractorOptic": {
        "input_types": ["RepositoryFiles"],
        "output_types": ["RepositoryMapView"],
        "optional_output_types": [],
        "read_projection": ["RepositoryFiles", "docs/agents/", "docs/PLAN.md"],
        "lineage": ["RepositoryFiles"],
        "status": "completed",
        "written_artifact_kind": None,
    },
    "AuditorOptic": {
        "input_types": ["RepositoryMapView", "IssueClaimView"],
        "output_types": ["AuditReport"],
        "optional_output_types": [],
        "read_projection": ["RepositoryMapView", "IssueClaimView", "docs/agents/"],
        "lineage": ["RepositoryMapView", "IssueClaimView"],
        "status": "completed",
        "written_artifact_kind": "audit_artifact",
    },
    "PlannerOptic": {
        "input_types": ["RepositoryMapView", "AuditReport"],
        "output_types": ["RepairPlan"],
        "optional_output_types": [],
        "read_projection": ["RepositoryMapView", "AuditReport"],
        "lineage": ["RepositoryMapView", "AuditReport"],
        "status": "proposal_only",
        "written_artifact_kind": "fix_artifact",
    },
    "PatcherOptic": {
        "input_types": ["AuditReport", "RepairPlan"],
        "output_types": ["PatchDiff"],
        "optional_output_types": [],
        "read_projection": ["AuditReport", "RepairPlan"],
        "lineage": ["AuditReport", "RepairPlan"],
        "status": "denied",
        "written_artifact_kind": None,
    },
    "ReviewerOptic": {
        "input_types": [
            "RepositoryMapView",
            "AuditReport",
            "RepairPlan",
            "PatchDiff",
        ],
        "output_types": ["ReviewReport"],
        "optional_output_types": ["AgentMapDelta"],
        "read_projection": [
            "RepositoryMapView",
            "AuditReport",
            "RepairPlan",
            "PatchDiff",
        ],
        "lineage": [
            "RepositoryMapView",
            "AuditReport",
            "RepairPlan",
            "PatchDiff",
        ],
        "status": "completed_structural_no_patchdiff",
        "written_artifact_kind": None,
    },
}


def run_declared_bai(
    *,
    argv: list[str],
    bai_home: Path,
    cwd: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    script = (
        "import importlib, sys; "
        "module_name, func_name = sys.argv[1].split(':'); "
        "func = getattr(importlib.import_module(module_name), func_name); "
        "raise SystemExit(func(sys.argv[2:]))"
    )
    env = os.environ.copy()
    env["BAI_HOME"] = str(bai_home)
    src_path = str((cwd / "src").resolve())
    env["PYTHONPATH"] = (
        src_path
        if not env.get("PYTHONPATH")
        else os.pathsep.join([src_path, env["PYTHONPATH"]])
    )
    return subprocess.run(
        [sys.executable, "-c", script, DECLARED_CLI_TARGET, *argv],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def add_workspace(runtime: RuntimePaths, workspace_root: Path) -> dict[str, Any]:
    result = run_declared_bai(
        argv=["workspace", "add", "demo", str(workspace_root)],
        bai_home=runtime.home,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    workspace = load_json_output(result.stdout)
    assert_runtime_artifacts(
        workspace_config_artifact_paths(runtime, workspace["workspace_id"]),
        runtime=runtime,
        workspace_root=workspace_root,
    )
    assert_under(workspace["sandbox_path"], runtime.sandbox)
    assert_not_under(workspace["sandbox_path"], workspace_root)
    assert_not_under(workspace["sandbox_path"], REPO_ROOT)
    return workspace


def allow_test(
    bai_home: Path,
    workspace_id: str,
    argv: list[str],
    *,
    writable_path: Path | None = None,
) -> dict[str, Any]:
    command = ["workspace", "allow-test", workspace_id, "--argv", json.dumps(argv)]
    if writable_path is not None:
        command.extend(["--writable-path", str(writable_path)])
    result = run_declared_bai(argv=command, bai_home=bai_home)
    assert result.returncode == 0
    assert result.stderr == ""
    updated = load_json_output(result.stdout)
    assert updated["command_policy"]["test"]["allowed_argv"][-1] == argv
    return updated


def workspace_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def workspace_config_artifact_paths(
    runtime: RuntimePaths, workspace_id: str
) -> list[Path]:
    paths = [runtime.config / "workspaces" / f"{workspace_id}.json"]
    name_directory = runtime.config / "workspace_names"
    if name_directory.exists():
        paths.extend(sorted(name_directory.glob("*.json")))
    return paths


def runtime_memory_artifact_paths(runtime: RuntimePaths) -> list[Path]:
    return sorted(runtime.memory.glob("*/*/*.json"))


def assert_runtime_artifacts(
    paths: list[str | Path],
    *,
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    assert_not_under(runtime.home, workspace_root)
    assert_not_under(runtime.home, REPO_ROOT)
    for runtime_path in [
        runtime.config,
        runtime.state,
        runtime.memory,
        runtime.cache,
        runtime.sandbox,
    ]:
        assert_under(runtime_path, runtime.home)
        assert_not_under(runtime_path, workspace_root)
        assert_not_under(runtime_path, REPO_ROOT)
    for path in paths:
        assert Path(path).exists(), path
        assert_under(path, runtime.home)
        assert_not_under(path, workspace_root)
        assert_not_under(path, REPO_ROOT)


def result_artifact_paths(result: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in [
        "workflow_artifact",
        "context_artifact",
        "scheduler_artifact",
        "approval_artifact",
        "memory_artifact",
        "working_memory_artifact",
        "audit_artifact",
        "fix_artifact",
        "optic_trace_artifact",
    ]:
        if result.get(key):
            paths.append(result[key])
    for key in ["approval_artifacts", "event_artifacts", "memory_artifacts"]:
        paths.extend(result.get(key, []))
    return list(dict.fromkeys(paths))


def approval_kinds(paths: list[str]) -> list[str]:
    return [read_json(path)["kind"] for path in paths]


def mutation_approvals(paths: list[str]) -> list[dict[str, Any]]:
    return [
        read_json(path)
        for path in paths
        if read_json(path)["kind"] == "mutation_approval"
    ]


def event_types(paths: list[str]) -> list[str]:
    return [read_json(path)["event_type"] for path in paths]


def modify_request(path: Path, content: str) -> str:
    return f"propose-modify {shlex.quote(str(path))} --content {shlex.quote(content)}"


def role_step(trace: dict[str, Any], optic_id: str) -> dict[str, Any]:
    for step in trace["role_steps"]:
        if step["optic_id"] == optic_id:
            return step
    raise AssertionError(f"missing role step: {optic_id}")


def write_bug_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace_root = tmp_path / "bug-workspace"
    workspace_root.mkdir()
    source = workspace_root / "calc.py"
    notes = workspace_root / "notes.txt"
    source.write_text("def add(a, b):\n    return a - b\n")
    notes.write_text("manual notes\n")
    return workspace_root, source, notes


def test_scaffold_dev_task_records_plan_audit_and_fix_without_source_mutation(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    workspace = add_workspace(runtime, workspace_root)
    before = workspace_snapshot(workspace_root)

    run = run_declared_bai(
        argv=["run", "--workspace", workspace["workspace_id"], "dev plan only"],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    workflow = read_json(result["workflow_artifact"])
    audit = read_json(result["audit_artifact"])

    assert result["status"] == "success"
    assert result["workflow_mode"] == "dev"
    assert result["applied_changes"] == []
    assert result["denied_changes"] == []
    assert [node["id"] for node in workflow["nodes"]] == DEV_NODE_IDS
    assert workflow["nodes"][1]["approval_required"] is True
    assert workflow["nodes"][1]["can_mutate"] is True
    assert workflow["nodes"][4]["can_mutate"] is False
    assert workflow["nodes"][5]["auto_apply"] is False
    assert result["node_results"]["code"]["status"] == "skipped"
    assert result["node_results"]["audit"]["status"] == "passed_with_notes"
    assert result["node_results"]["fix"]["status"] == "skipped"
    assert result["fix_artifact"] is None
    assert result["fix_proposals"] == []
    assert audit["findings"] == [
        {
            "severity": "info",
            "code": "no_test_command",
            "message": "no trusted test command ran",
        }
    ]
    assert audit["source_artifacts"]["workflow_artifact"] == result["workflow_artifact"]
    assert event_types(result["event_artifacts"]) == ["started", "completed"]
    assert workspace_snapshot(workspace_root) == before
    assert_runtime_artifacts(
        result_artifact_paths(result),
        runtime=runtime,
        workspace_root=workspace_root,
    )


def test_unapproved_dev_fix_proposal_is_audited_but_not_applied(
    runtime: RuntimePaths,
    tmp_path: Path,
) -> None:
    workspace_root, source, notes = write_bug_workspace(tmp_path)
    workspace = add_workspace(runtime, workspace_root)
    before = workspace_snapshot(workspace_root)
    fixed_content = "def add(a, b):\n    return a + b\n"

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"dev {modify_request(source, fixed_content)}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    workflow = read_json(result["workflow_artifact"])
    audit = read_json(result["audit_artifact"])
    fix = read_json(result["fix_artifact"])

    assert result["status"] == "completed_with_denials"
    assert result["applied_changes"] == []
    assert result["denied_changes"] == [
        {
            "operation": "modify",
            "path": str(source.resolve()),
            "reason": "mutation requires harness approval",
            "status": "denied",
        }
    ]
    assert workspace_snapshot(workspace_root) == before
    assert source.read_text() == "def add(a, b):\n    return a - b\n"
    assert notes.read_text() == "manual notes\n"
    assert approval_kinds(result["approval_artifacts"]) == ["agent_output_acceptance"]
    assert result["node_results"]["code"]["status"] == "denied"
    assert result["node_results"]["fix"]["status"] == "proposal_only"
    assert workflow["artifacts"]["node_results"] == result["node_results"]
    assert any(
        finding["code"] == "mutation_denied"
        and finding["path"] == str(source.resolve())
        and finding["operation"] == "modify"
        for finding in audit["findings"]
    )
    assert fix["status"] == "proposal_only"
    assert fix["source_audit_artifact"] == result["audit_artifact"]
    assert fix["proposals"] == result["fix_proposals"]
    assert fix["proposals"] == [
        {
            "status": "proposal_only",
            "kind": "request_mutation_approval",
            "message": "request approval for denied mutation path",
            "path": str(source.resolve()),
            "operation": "modify",
            "source_finding_code": "mutation_denied",
            "auto_apply": False,
        }
    ]
    assert_runtime_artifacts(
        result_artifact_paths(result),
        runtime=runtime,
        workspace_root=workspace_root,
    )


def test_manual_approval_applies_one_scoped_change_and_records_reviewable_evidence(
    runtime: RuntimePaths,
    tmp_path: Path,
) -> None:
    workspace_root, source, notes = write_bug_workspace(tmp_path)
    workspace = add_workspace(runtime, workspace_root)
    before = workspace_snapshot(workspace_root)
    original_source = source.read_text()
    fixed_content = "def add(a, b):\n    return a + b\n"
    argv = [
        sys.executable,
        "-B",
        "-c",
        (
            "from pathlib import Path; "
            f"assert Path('calc.py').read_text() == {fixed_content!r}; "
            "print('verified')"
        ),
    ]
    allow_test(runtime.home, workspace["workspace_id"], argv)

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(source),
            f"dev {modify_request(source, fixed_content)} test {shlex.join(argv)}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    workflow = read_json(result["workflow_artifact"])
    audit = read_json(result["audit_artifact"])
    approval_records = [read_json(path) for path in result["approval_artifacts"]]
    approvals = mutation_approvals(result["approval_artifacts"])

    expected_after = dict(before)
    expected_after["calc.py"] = fixed_content.encode("utf-8")
    assert workspace_snapshot(workspace_root) == expected_after
    assert source.read_text() == fixed_content
    assert notes.read_text() == "manual notes\n"
    assert sorted(path.name for path in workspace_root.iterdir()) == ["calc.py", "notes.txt"]
    assert result["applied_changes"] == [{"operation": "modify", "path": str(source.resolve())}]
    assert result["denied_changes"] == []
    assert result["test_runs"][0]["argv"] == argv
    assert result["test_runs"][0]["cwd"] == str(workspace_root.resolve())
    assert result["test_runs"][0]["exit_code"] == 0
    assert result["test_runs"][0]["stdout_preview"] == "verified\n"
    assert workflow["artifacts"]["test_runs"] == result["test_runs"]
    assert workflow["artifacts"]["node_results"]["code"]["status"] == "completed"
    assert workflow["artifacts"]["node_results"]["test"]["status"] == "completed"
    assert [approval["kind"] for approval in approval_records] == [
        "agent_output_acceptance",
        "mutation_approval",
    ]
    assert len(approvals) == 1
    approval = approvals[0]
    scope = approval["approval_scope"]
    assert approval["workspace_id"] == workspace["workspace_id"]
    assert approval["task_id"] == result["task_id"]
    assert approval["bound_to_artifacts"] == [result["approval_artifact"]]
    assert scope == {
        "workspace_id": workspace["workspace_id"],
        "approved_path": str(source.resolve()),
        "operation": "modify",
        "approving_mechanism": "cli_flag",
        "task_id": result["task_id"],
        "content_sha256": hashlib.sha256(fixed_content.encode("utf-8")).hexdigest(),
        "expected_preimage": {
            "exists": True,
            "sha256": hashlib.sha256(original_source.encode("utf-8")).hexdigest(),
        },
    }
    assert audit["findings"] == [
        {
            "severity": "info",
            "code": "trusted_test_command_no_sandbox",
            "message": "test command ran as trusted direct execution without a write sandbox",
        }
    ]
    assert result["fix_artifact"] is None
    assert_runtime_artifacts(
        result_artifact_paths(result),
        runtime=runtime,
        workspace_root=workspace_root,
    )


def test_manual_approval_rejects_outside_or_unrequested_paths(
    runtime: RuntimePaths,
    tmp_path: Path,
) -> None:
    workspace_root, source, notes = write_bug_workspace(tmp_path)
    workspace = add_workspace(runtime, workspace_root)
    requested = workspace_root / "requested.md"
    unrequested = workspace_root / "unrequested.md"
    outside = tmp_path / "outside.md"
    before = workspace_snapshot(workspace_root)

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(unrequested),
            f"dev propose-create {requested}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    assert result["status"] == "completed_with_denials"
    assert result["applied_changes"] == []
    assert result["denied_changes"] == [
        {
            "operation": "create",
            "path": str(requested.resolve()),
            "reason": "mutation requires harness approval",
            "status": "denied",
        }
    ]
    assert not requested.exists()
    assert not unrequested.exists()
    assert workspace_snapshot(workspace_root) == before
    assert not mutation_approvals(result["approval_artifacts"])
    assert_runtime_artifacts(
        result_artifact_paths(result),
        runtime=runtime,
        workspace_root=workspace_root,
    )

    before_failed = workspace_snapshot(workspace_root)
    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(outside),
            f"dev propose-create {outside}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 2
    assert run.stdout == ""
    assert "outside allowed workspace paths" in run.stderr
    assert not outside.exists()
    assert workspace_snapshot(workspace_root) == before_failed
    assert source.read_text() == "def add(a, b):\n    return a - b\n"
    assert notes.read_text() == "manual notes\n"
    assert not any(
        read_json(path)["kind"] == "mutation_approval"
        for path in sorted((runtime.state / "approvals").glob("*.json"))
    )
    failed_workflow_paths = [
        path
        for path in sorted((runtime.state / "workflows").glob("*.json"))
        if read_json(path)["status"] == "failed"
    ]
    failed_workflows = [read_json(path) for path in failed_workflow_paths]
    assert len(failed_workflows) == 1
    assert failed_workflows[0]["artifacts"]["memory_artifacts"] == []
    assert not any(
        str(memory.get("content", {}).get("task_id", ""))
        == failed_workflows[0]["task_id"]
        for memory in (
            read_json(path) for path in sorted((runtime.memory).glob("*/*/*.json"))
        )
    )
    assert_runtime_artifacts(
        [
            path
            for path in [
            *failed_workflow_paths,
            failed_workflows[0]["artifacts"]["context_artifact"],
            failed_workflows[0]["artifacts"].get("audit_artifact"),
            failed_workflows[0]["artifacts"].get("fix_artifact"),
            failed_workflows[0]["artifacts"].get("optic_trace_artifact"),
            *failed_workflows[0]["artifacts"]["approval_artifacts"],
            *failed_workflows[0]["artifacts"]["event_artifacts"],
            *sorted((runtime.state / "scheduler").glob("*.json")),
            ]
            if path
        ],
        runtime=runtime,
        workspace_root=workspace_root,
    )


def test_trusted_test_command_records_no_sandbox_evidence_for_manual_review(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    workspace = add_workspace(runtime, workspace_root)
    marker = workspace_root / "trusted-test-marker.txt"
    before = workspace_snapshot(workspace_root)
    argv = [
        sys.executable,
        "-B",
        "-c",
        (
            "from pathlib import Path; "
            "Path('trusted-test-marker.txt').write_text('trusted write\\n'); "
            "print('trusted direct execution')"
        ),
    ]
    allow_test(runtime.home, workspace["workspace_id"], argv)

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"dev test {shlex.join(argv)}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    workflow = read_json(result["workflow_artifact"])
    audit = read_json(result["audit_artifact"])

    expected_after = dict(before)
    expected_after["trusted-test-marker.txt"] = b"trusted write\n"
    assert workspace_snapshot(workspace_root) == expected_after
    assert marker.read_text() == "trusted write\n"
    assert result["test_runs"][0]["argv"] == argv
    assert result["test_runs"][0]["write_enforcement"] == "trusted_command_no_sandbox"
    assert result["test_runs"][0]["declared_writable_paths"] == []
    assert workflow["artifacts"]["test_runs"] == result["test_runs"]
    assert workflow["artifacts"]["node_results"]["test"]["status"] == "completed"
    assert result["audit_findings"] == [
        {
            "severity": "info",
            "code": "trusted_test_command_no_sandbox",
            "message": "test command ran as trusted direct execution without a write sandbox",
        }
    ]
    assert audit["status"] == "passed_with_notes"
    assert audit["findings"] == result["audit_findings"]
    assert result["fix_artifact"] is None
    assert result["node_results"]["fix"]["status"] == "skipped"
    assert_runtime_artifacts(
        result_artifact_paths(result),
        runtime=runtime,
        workspace_root=workspace_root,
    )


def test_failing_test_records_failed_audit_without_success_memory(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    workspace = add_workspace(runtime, workspace_root)
    before = workspace_snapshot(workspace_root)
    argv = [
        sys.executable,
        "-B",
        "-c",
        "import sys; sys.stderr.write('expected failure\\n'); sys.exit(9)",
    ]
    allow_test(runtime.home, workspace["workspace_id"], argv)

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"dev test {shlex.join(argv)}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 2
    assert run.stdout == ""
    assert "test command failed with exit code 9" in run.stderr
    assert workspace_snapshot(workspace_root) == before
    [workflow_path] = sorted((runtime.state / "workflows").glob("*.json"))
    workflow = read_json(workflow_path)
    audit = read_json(workflow["artifacts"]["audit_artifact"])
    fix = read_json(workflow["artifacts"]["fix_artifact"])
    events = [read_json(path) for path in workflow["artifacts"]["event_artifacts"]]

    assert workflow["status"] == "failed"
    assert [event["event_type"] for event in events] == ["started", "failed"]
    assert workflow["artifacts"]["memory_artifacts"] == []
    assert workflow["artifacts"]["test_runs"][0]["argv"] == argv
    assert workflow["artifacts"]["test_runs"][0]["exit_code"] == 9
    assert workflow["artifacts"]["test_runs"][0]["stderr_preview"] == "expected failure\n"
    assert workflow["artifacts"]["test_runs"][0]["write_enforcement"] == (
        "trusted_command_no_sandbox"
    )
    assert audit["status"] == "failed"
    assert {
        "severity": "error",
        "code": "test_command_failed",
        "message": "test command failed with exit code 9",
        "exit_code": 9,
    } in audit["findings"]
    assert fix["status"] == "proposal_only"
    assert fix["proposals"] == [
        {
            "status": "proposal_only",
            "kind": "manual_test_failure_review",
            "message": "inspect failing test output and update code manually",
            "source_finding_code": "test_command_failed",
            "auto_apply": False,
        }
    ]
    assert not (runtime.memory / workspace["workspace_id"]).exists()
    assert runtime_memory_artifact_paths(runtime) == []
    assert_runtime_artifacts(
        [
            workflow_path,
            workflow["artifacts"]["context_artifact"],
            workflow["artifacts"]["audit_artifact"],
            workflow["artifacts"]["fix_artifact"],
            workflow["artifacts"]["optic_trace_artifact"],
            *workflow["artifacts"]["approval_artifacts"],
            *workflow["artifacts"]["event_artifacts"],
            *sorted((runtime.state / "scheduler").glob("*.json")),
        ],
        runtime=runtime,
        workspace_root=workspace_root,
    )


def test_background_dev_task_is_deferred_metadata_only(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    workspace = add_workspace(runtime, workspace_root)
    target = workspace_root / "background-created.md"
    before = workspace_snapshot(workspace_root)

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(target),
            "--background",
            f"dev propose-create {target}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    scheduler = read_json(result["scheduler_artifact"])

    assert result == {
        "background": True,
        "scheduler_artifact": result["scheduler_artifact"],
        "status": "deferred",
        "workspace_id": workspace["workspace_id"],
    }
    assert scheduler["workspace_id"] == workspace["workspace_id"]
    assert scheduler["priority"] == "background"
    assert scheduler["lifecycle_status"] == "deferred"
    assert scheduler["execution_state"] == "deferred_metadata_only"
    assert scheduler["preemptible"] is True
    assert "harness_task_id" not in scheduler
    assert "run_status" not in scheduler
    assert "executed" not in scheduler
    assert not target.exists()
    assert workspace_snapshot(workspace_root) == before
    assert_runtime_artifacts(
        [result["scheduler_artifact"]],
        runtime=runtime,
        workspace_root=workspace_root,
    )
    for name in ["events", "workflows", "context", "approvals", "audits", "fixes"]:
        assert not (runtime.state / name).exists()
    assert not (runtime.memory / workspace["workspace_id"]).exists()


def test_bugfix_optic_loop_validates_trace_without_running_roles_as_agents(
    runtime: RuntimePaths,
    tmp_path: Path,
) -> None:
    workspace_root, source, _ = write_bug_workspace(tmp_path)
    workspace = add_workspace(runtime, workspace_root)
    fixed_content = "def add(a, b):\n    return a + b\n"
    before = workspace_snapshot(workspace_root)

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"dev {modify_request(source, fixed_content)}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    workflow = read_json(result["workflow_artifact"])
    optic_trace_path = Path(result["optic_trace_artifact"])
    trace = read_json(optic_trace_path)
    registry = build_bug_fix_structural_loop_registry()
    validated = registry.validate_registered("BugFixStructuralLoopOptic")

    assert workspace_snapshot(workspace_root) == before
    assert result["applied_changes"] == []
    assert result["denied_changes"] == [
        {
            "operation": "modify",
            "path": str(source.resolve()),
            "reason": "mutation requires harness approval",
            "status": "denied",
        }
    ]
    assert result["plan"]["agent_kind"] == "plan"
    assert all(
        node["kind"] not in {"agent_to_agent", "parallel_worker"}
        for node in workflow["nodes"]
    )
    assert workflow["artifacts"]["audit_artifact"] == result["audit_artifact"]
    assert workflow["artifacts"]["fix_artifact"] == result["fix_artifact"]
    assert workflow["artifacts"]["optic_trace_artifact"] == result["optic_trace_artifact"]
    assert trace["composition_id"] == validated.composition_id
    assert trace["composition_validated"] is True
    assert trace["source_optics"] == BUGFIX_OPTIC_IDS
    assert trace["input_types"] == list(validated.input_types)
    assert trace["output_types"] == list(validated.output_types)
    assert trace["optional_output_types"] == list(validated.optional_output_types)
    assert [step["optic_id"] for step in trace["role_steps"]] == BUGFIX_OPTIC_IDS
    assert [
        (
            handoff["from_optic"],
            handoff["output_type"],
            handoff["to_optic"],
            handoff["input_type"],
        )
        for handoff in trace["handoffs"]
    ] == BUGFIX_HANDOFFS
    assert trace["source_artifacts"] == {
        "context_artifact": result["context_artifact"],
        "approval_artifacts": result["approval_artifacts"],
        "event_artifacts": result["event_artifacts"],
        "audit_artifact": result["audit_artifact"],
        "fix_artifact": result["fix_artifact"],
    }
    assert trace["non_goals"] == {
        "agent_to_agent_chat": False,
        "autonomous_patch_applied": False,
        "parallel_execution": False,
    }
    for step in trace["role_steps"]:
        assert set(step) == {
            "optic_id",
            "input_types",
            "output_types",
            "optional_output_types",
            "read_projection",
            "write_surface",
            "lineage",
            "status",
            "written_artifacts",
            "mutation_evidence",
        }
        assert not any(
            key in step for key in ("agent_id", "agent_kind", "agent_execution", "tool_calls")
        )
        expected = BUGFIX_ROLE_TRACE_EXPECTATIONS[step["optic_id"]]
        assert step["input_types"] == expected["input_types"]
        assert step["output_types"] == expected["output_types"]
        assert step["optional_output_types"] == expected["optional_output_types"]
        assert step["read_projection"] == expected["read_projection"]
        assert step["lineage"] == expected["lineage"]
        assert step["status"] == expected["status"]
        assert step["write_surface"] == list(registry.optic(step["optic_id"]).write_surface)
        if expected["written_artifact_kind"] is None:
            assert step["written_artifacts"] == []
            continue
        assert step["written_artifacts"]
        assert all(
            record["kind"] == expected["written_artifact_kind"]
            for record in step["written_artifacts"]
        )
        for record in step["written_artifacts"]:
            assert Path(record["path"]).exists(), record
            assert_under(record["path"], runtime.home)
            assert_not_under(record["path"], workspace_root)
            assert_not_under(record["path"], REPO_ROOT)
            if record["kind"] == "audit_artifact":
                assert record["path"] == result["audit_artifact"]
            if record["kind"] == "fix_artifact":
                assert record["path"] == result["fix_artifact"]
    assert role_step(trace, "PatcherOptic")["status"] == "denied"
    assert role_step(trace, "PatcherOptic")["mutation_evidence"] == []
    assert role_step(trace, "ReviewerOptic")["mutation_evidence"] == []
    assert not mutation_approvals(result["approval_artifacts"])

    with pytest.raises(OpticCategoryError, match="write target outside declared write surface"):
        registry.validate_write("AuditorOptic", "src/bai/core/policy.py")
    with pytest.raises(OpticCategoryError, match="write target outside declared write surface"):
        registry.validate_write("ReviewerOptic", "docs/agents/runtime-map.md")
    registry.validate_write(
        "ReviewerOptic",
        "docs/agents/runtime-map.md",
        output_type="AgentMapDelta",
    )
    assert_runtime_artifacts(
        result_artifact_paths(result),
        runtime=runtime,
        workspace_root=workspace_root,
    )
