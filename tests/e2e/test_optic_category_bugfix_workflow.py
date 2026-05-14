from __future__ import annotations

import json
import shlex
import sys
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from bai.optic_category import (
    OpticCategoryError,
    build_bug_fix_structural_loop_composition,
    build_bug_fix_structural_loop_registry,
)
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


def write_calc_bug_workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    workspace_root = tmp_path / "calculator-workspace"
    workspace_root.mkdir()
    calc = workspace_root / "calc.py"
    test_calc = workspace_root / "test_calc.py"
    untouched = workspace_root / "notes.txt"
    calc.write_text("def add(a, b):\n    return a - b\n")
    test_calc.write_text(
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )
    untouched.write_text("unrelated\n")
    return workspace_root, calc, test_calc, untouched


def calc_test_argv(test_calc: Path) -> list[str]:
    return [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        str(test_calc),
    ]


def mutation_request(path: Path, content: str) -> str:
    return f"propose-modify {shlex.quote(str(path))} --content {shlex.quote(content)}"


def approval_kinds(paths: list[str]) -> list[str]:
    return [read_json(path)["kind"] for path in paths]


def only_mutation_approval(paths: list[str]) -> tuple[str, dict]:
    approvals = [(path, read_json(path)) for path in paths]
    mutation_approvals = [
        (path, approval)
        for path, approval in approvals
        if approval["kind"] == "mutation_approval"
    ]
    assert len(mutation_approvals) == 1
    return mutation_approvals[0]


def role_step(trace: dict, optic_id: str) -> dict:
    for step in trace["role_steps"]:
        if step["optic_id"] == optic_id:
            return step
    raise AssertionError(f"missing role step: {optic_id}")


def assert_result_artifacts_under_runtime(
    result: dict,
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    artifacts = [
        result["workflow_artifact"],
        result["context_artifact"],
        result["audit_artifact"],
        result["optic_trace_artifact"],
        result["scheduler_artifact"],
        *result["approval_artifacts"],
        *result["event_artifacts"],
        *result["memory_artifacts"],
    ]
    if result["fix_artifact"] is not None:
        artifacts.append(result["fix_artifact"])
    for artifact in artifacts:
        assert_under(artifact, runtime.home)
        assert_not_under(artifact, workspace_root)


def test_bugfix_structural_loop_records_valid_role_trace(
    runtime: RuntimePaths,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root, calc, test_calc, untouched = write_calc_bug_workspace(tmp_path)
    workspace = add_workspace(capsys, workspace_root)
    test_argv = calc_test_argv(test_calc)
    allow_test(capsys, workspace["workspace_id"], test_argv)

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"dev test {shlex.join(test_argv)}",
        ],
    )

    assert code == 2
    assert output == ""
    assert "test command failed" in error
    assert calc.read_text() == "def add(a, b):\n    return a - b\n"
    assert sorted(path.name for path in workspace_root.iterdir()) == [
        "calc.py",
        "notes.txt",
        "test_calc.py",
    ]
    assert untouched.read_text() == "unrelated\n"

    workflow_path = next((runtime.state / "workflows").glob("*.json"))
    workflow = read_json(workflow_path)
    assert workflow["workflow_mode"] == "dev"
    assert workflow["status"] == "failed"
    for artifact in [
        workflow_path,
        workflow["artifacts"]["context_artifact"],
        workflow["artifacts"]["audit_artifact"],
        workflow["artifacts"]["fix_artifact"],
        *workflow["artifacts"]["approval_artifacts"],
        *workflow["artifacts"]["event_artifacts"],
    ]:
        assert_under(artifact, runtime.home)
        assert_not_under(artifact, workspace_root)
    scheduler_artifact = next((runtime.state / "scheduler").glob("*.json"))
    assert_under(scheduler_artifact, runtime.home)
    assert read_json(scheduler_artifact)["lifecycle_status"] == "failed"

    optic_trace_path = Path(workflow["artifacts"]["optic_trace_artifact"])
    assert_under(optic_trace_path, runtime.home)
    assert_not_under(optic_trace_path, workspace_root)
    trace = read_json(optic_trace_path)

    expected_roles = {
        "RoadmapExtractorOptic": {
            "input_types": ["RepositoryFiles"],
            "output_types": ["RepositoryMapView"],
            "lineage": ["RepositoryFiles"],
            "status": "completed",
            "written_artifact_kind": None,
        },
        "AuditorOptic": {
            "input_types": ["RepositoryMapView", "IssueClaimView"],
            "output_types": ["AuditReport"],
            "lineage": ["RepositoryMapView", "IssueClaimView"],
            "status": "completed",
            "written_artifact_kind": "audit_artifact",
        },
        "PlannerOptic": {
            "input_types": ["RepositoryMapView", "AuditReport"],
            "output_types": ["RepairPlan"],
            "lineage": ["RepositoryMapView", "AuditReport"],
            "status": "proposal_only",
            "written_artifact_kind": "fix_artifact",
        },
        "PatcherOptic": {
            "input_types": ["AuditReport", "RepairPlan"],
            "output_types": ["PatchDiff"],
            "lineage": ["AuditReport", "RepairPlan"],
            "status": "not_applied_no_patchdiff",
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
    expected_handoffs = [
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

    assert trace["composition_id"] == "BugFixStructuralLoopOptic"
    assert trace["composition_validated"] is True
    assert trace["source_optics"] == [
        "RoadmapExtractorOptic",
        "AuditorOptic",
        "PlannerOptic",
        "PatcherOptic",
        "ReviewerOptic",
    ]
    assert [step["optic_id"] for step in trace["role_steps"]] == trace["source_optics"]
    assert [
        (
            handoff["from_optic"],
            handoff["output_type"],
            handoff["to_optic"],
            handoff["input_type"],
        )
        for handoff in trace["handoffs"]
    ] == expected_handoffs
    assert all(handoff["output_type"] == handoff["input_type"] for handoff in trace["handoffs"])

    for step in trace["role_steps"]:
        expected = expected_roles[step["optic_id"]]
        assert step["input_types"] == expected["input_types"]
        assert step["output_types"] == expected["output_types"]
        assert step["lineage"] == expected["lineage"]
        assert step["status"] == expected["status"]
        if expected["written_artifact_kind"] is None:
            assert step["written_artifacts"] == []
            continue
        assert step["written_artifacts"]
        assert all(record["kind"] == expected["written_artifact_kind"] for record in step["written_artifacts"])
        for record in step["written_artifacts"]:
            assert_under(record["path"], runtime.home)
            assert_not_under(record["path"], workspace_root)
            assert Path(record["path"]).exists()
            if record["kind"] == "audit_artifact":
                assert record["path"] == workflow["artifacts"]["audit_artifact"]
            if record["kind"] == "fix_artifact":
                assert record["path"] == workflow["artifacts"]["fix_artifact"]

    audit = read_json(workflow["artifacts"]["audit_artifact"])
    fix = read_json(workflow["artifacts"]["fix_artifact"])
    assert any(finding["code"] == "test_command_failed" for finding in audit["findings"])
    assert fix["proposals"][0]["auto_apply"] is False
    assert workflow["artifacts"]["test_runs"][0]["write_enforcement"] == (
        "trusted_command_no_sandbox"
    )
    assert workflow["artifacts"]["applied_changes"] == []
    assert workflow["artifacts"]["denied_changes"] == []
    assert workflow["artifacts"]["node_results"]["code"]["proposed_changes"] == []
    assert workflow["artifacts"]["node_results"]["code"]["applied_changes"] == []
    assert workflow["artifacts"]["node_results"]["code"]["denied_changes"] == []
    approval_kinds = [
        read_json(path)["kind"] for path in workflow["artifacts"]["approval_artifacts"]
    ]
    assert approval_kinds == ["agent_output_acceptance"]
    assert "mutation_approval" not in approval_kinds
    assert not (runtime.memory / workspace["workspace_id"]).exists()


def test_bugfix_composition_missing_handoff_fails_closed(
    runtime: RuntimePaths,
) -> None:
    registry = build_bug_fix_structural_loop_registry()
    composition = build_bug_fix_structural_loop_composition()
    missing_audit_to_planner = composition.__class__(
        composition_id="BrokenBugFixStructuralLoopOptic",
        source_optics=composition.source_optics,
        handoffs=tuple(
            handoff
            for handoff in composition.handoffs
            if not (
                handoff.from_optic == "AuditorOptic"
                and handoff.to_optic == "PlannerOptic"
                and handoff.output_type == "AuditReport"
            )
        ),
        input_types=composition.input_types,
        output_types=composition.output_types,
        optional_output_types=composition.optional_output_types,
    )

    with pytest.raises(OpticCategoryError, match="PlannerOptic requires input type AuditReport"):
        registry.validate_composition(missing_audit_to_planner)

    assert "BrokenBugFixStructuralLoopOptic" not in registry.compositions
    assert not (runtime.memory).exists()
    assert not (runtime.state / "workflows" / "BrokenBugFixStructuralLoopOptic.json").exists()


def test_bugfix_role_write_surface_violation_fails_closed(tmp_path: Path) -> None:
    registry = build_bug_fix_structural_loop_registry()
    before_surfaces = {
        optic_id: (
            registry.optic(optic_id).write_surface,
            dict(registry.optic(optic_id).conditional_write_surface),
        )
        for optic_id in ("AuditorOptic", "PlannerOptic", "PatcherOptic", "ReviewerOptic")
    }
    registry.validate_registered("BugFixStructuralLoopOptic")
    after_surfaces = {
        optic_id: (
            registry.optic(optic_id).write_surface,
            dict(registry.optic(optic_id).conditional_write_surface),
        )
        for optic_id in before_surfaces
    }
    source_file = tmp_path / "workspace" / "src" / "service.py"
    docs_agents_file = tmp_path / "workspace" / "docs" / "agents" / "map.md"
    source_file.parent.mkdir(parents=True)
    docs_agents_file.parent.mkdir(parents=True)
    source_file.write_text("value = 1\n")
    docs_agents_file.write_text("agent map\n")

    assert after_surfaces == before_surfaces
    assert "docs/agents/" not in registry.optic("ReviewerOptic").write_surface
    registry.validate_write(
        "ReviewerOptic",
        "docs/agents/map.md",
        output_type="AgentMapDelta",
    )

    with pytest.raises(OpticCategoryError, match="write target outside declared write surface"):
        registry.validate_write("AuditorOptic", "src/service.py")

    with pytest.raises(OpticCategoryError, match="write target outside declared write surface"):
        registry.validate_write("PlannerOptic", "docs/agents/map.md")

    with pytest.raises(OpticCategoryError, match="write target outside declared write surface"):
        registry.validate_write("PatcherOptic", "docs/agents/map.md")

    with pytest.raises(OpticCategoryError, match="write target outside declared write surface"):
        registry.validate_write("ReviewerOptic", "docs/agents/map.md")

    with pytest.raises(OpticCategoryError, match="write target outside declared write surface"):
        registry.validate_write(
            "ReviewerOptic",
            "docs/agents/map.md",
            output_type="ReviewReport",
        )

    assert source_file.read_text() == "value = 1\n"
    assert docs_agents_file.read_text() == "agent map\n"


def test_bugfix_approved_patch_applies_only_to_allowed_file(
    runtime: RuntimePaths,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root, calc, test_calc, untouched = write_calc_bug_workspace(tmp_path)
    workspace = add_workspace(capsys, workspace_root)
    test_argv = calc_test_argv(test_calc)
    allow_test(capsys, workspace["workspace_id"], test_argv)
    fixed_content = "def add(a, b):\n    return a + b\n"

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(calc),
            f"dev {mutation_request(calc, fixed_content)} test {shlex.join(test_argv)}",
        ],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    workflow = read_json(result["workflow_artifact"])
    trace = read_json(result["optic_trace_artifact"])
    mutation_approval_path, mutation_approval = only_mutation_approval(
        result["approval_artifacts"]
    )

    assert calc.read_text() == fixed_content
    assert test_calc.read_text() == (
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )
    assert untouched.read_text() == "unrelated\n"
    assert sorted(path.name for path in workspace_root.iterdir()) == [
        "calc.py",
        "notes.txt",
        "test_calc.py",
    ]
    assert result["applied_changes"] == [{"operation": "modify", "path": str(calc.resolve())}]
    assert result["denied_changes"] == []
    assert_result_artifacts_under_runtime(result, runtime, workspace_root)
    assert mutation_approval["workspace_id"] == workspace["workspace_id"]
    assert mutation_approval["task_id"] == result["task_id"]
    assert mutation_approval["bound_to_artifacts"] == [result["approval_artifact"]]
    assert mutation_approval["approval_scope"]["approved_path"] == str(calc.resolve())
    assert mutation_approval["approval_scope"]["operation"] == "modify"
    assert mutation_approval["approval_scope"]["workspace_id"] == workspace["workspace_id"]
    assert mutation_approval["approval_scope"]["task_id"] == result["task_id"]
    assert mutation_approval["approval_scope"]["content_sha256"] == hashlib.sha256(
        fixed_content.encode("utf-8")
    ).hexdigest()
    assert mutation_approval["approval_scope"]["expected_preimage"] == {
        "exists": True,
        "sha256": hashlib.sha256(b"def add(a, b):\n    return a - b\n").hexdigest(),
    }
    assert result["test_runs"][0]["write_enforcement"] == "trusted_command_no_sandbox"
    assert workflow["artifacts"]["test_runs"][0]["write_enforcement"] == (
        "trusted_command_no_sandbox"
    )

    patcher = role_step(trace, "PatcherOptic")
    reviewer = role_step(trace, "ReviewerOptic")
    assert patcher["status"] == "applied"
    assert reviewer["status"] == "completed_structural"
    assert patcher["mutation_evidence"] == [
        {
            "path": str(calc.resolve()),
            "operation": "modify",
            "approval_artifact": mutation_approval_path,
        }
    ]
    assert reviewer["mutation_evidence"] == patcher["mutation_evidence"]
    for artifact in result["approval_artifacts"]:
        assert_under(artifact, runtime.home)
        assert_not_under(artifact, workspace_root)


def test_bugfix_unapproved_patch_remains_proposal_only(
    runtime: RuntimePaths,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root, calc, test_calc, untouched = write_calc_bug_workspace(tmp_path)
    workspace = add_workspace(capsys, workspace_root)
    fixed_content = "def add(a, b):\n    return a + b\n"

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"dev {mutation_request(calc, fixed_content)}",
        ],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    workflow = read_json(result["workflow_artifact"])
    trace = read_json(result["optic_trace_artifact"])

    assert result["status"] == "completed_with_denials"
    assert_result_artifacts_under_runtime(result, runtime, workspace_root)
    assert calc.read_text() == "def add(a, b):\n    return a - b\n"
    assert test_calc.read_text() == (
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )
    assert untouched.read_text() == "unrelated\n"
    assert result["applied_changes"] == []
    assert result["denied_changes"] == [
        {
            "operation": "modify",
            "path": str(calc.resolve()),
            "reason": "mutation requires harness approval",
            "status": "denied",
        }
    ]
    assert approval_kinds(result["approval_artifacts"]) == ["agent_output_acceptance"]
    assert workflow["artifacts"]["node_results"]["code"]["status"] == "denied"
    assert workflow["artifacts"]["node_results"]["fix"]["status"] == "proposal_only"
    assert read_json(result["fix_artifact"])["status"] == "proposal_only"
    assert role_step(trace, "PatcherOptic")["status"] == "denied"
    assert role_step(trace, "PatcherOptic")["mutation_evidence"] == []
    assert not any(
        read_json(path)["kind"] == "mutation_approval"
        for path in result["approval_artifacts"]
    )
    assert_under(result["fix_artifact"], runtime.home)
    assert_not_under(result["fix_artifact"], workspace_root)


def test_bugfix_composition_cannot_broaden_visibility(
    runtime: RuntimePaths,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = build_bug_fix_structural_loop_registry()
    composition = build_bug_fix_structural_loop_composition()
    with_extra_input = replace(
        composition,
        composition_id="SecretBroadenedBugFixStructuralLoopOptic",
        input_types=composition.input_types
        + (
            "InternalRiskScoreView",
            "ArbitrarySourceFileContent",
            "SecretTokenView",
            "OpaqueTelemetryEnvelope",
        ),
    )
    with_extra_output = replace(
        composition,
        composition_id="SecretOutputBugFixStructuralLoopOptic",
        output_types=composition.output_types + ("SecretTokenView",),
    )

    with pytest.raises(
        OpticCategoryError,
        match="undeclared visibility.*OpaqueTelemetryEnvelope",
    ):
        registry.validate_composition(with_extra_input)

    with pytest.raises(OpticCategoryError, match="not produced|undeclared visibility"):
        registry.validate_composition(with_extra_output)

    assert "SecretBroadenedBugFixStructuralLoopOptic" not in registry.compositions
    assert "SecretOutputBugFixStructuralLoopOptic" not in registry.compositions
    assert not (runtime.memory).exists()

    workspace_root, _, _, _ = write_calc_bug_workspace(tmp_path)
    workspace = add_workspace(capsys, workspace_root)
    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            (
                "dev plan only but expose SecretTokenView "
                "ArbitrarySourceFileContent OpaqueTelemetryEnvelope"
            ),
        ],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    trace = read_json(result["optic_trace_artifact"])
    assert trace["input_types"] == ["RepositoryFiles", "IssueClaimView"]
    assert trace["output_types"] == ["ReviewReport"]
    assert trace["optional_output_types"] == ["PatchDiff", "AgentMapDelta"]
    assert trace["source_optics"] == [
        "RoadmapExtractorOptic",
        "AuditorOptic",
        "PlannerOptic",
        "PatcherOptic",
        "ReviewerOptic",
    ]
    assert role_step(trace, "RoadmapExtractorOptic")["read_projection"] == [
        "RepositoryFiles",
        "docs/agents/",
        "docs/PLAN.md",
    ]
    assert role_step(trace, "AuditorOptic")["read_projection"] == [
        "RepositoryMapView",
        "IssueClaimView",
        "docs/agents/",
    ]
    assert role_step(trace, "PlannerOptic")["read_projection"] == [
        "RepositoryMapView",
        "AuditReport",
    ]
    assert role_step(trace, "PatcherOptic")["read_projection"] == [
        "AuditReport",
        "RepairPlan",
    ]
    assert role_step(trace, "ReviewerOptic")["read_projection"] == [
        "RepositoryMapView",
        "AuditReport",
        "RepairPlan",
        "PatchDiff",
    ]
