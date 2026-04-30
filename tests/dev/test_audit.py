from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.artifacts.audit import AuditStore, build_audit_result
from bai.artifacts.workflow import WorkflowStore
from bai.config.workspace import WorkspaceRecord, WorkspaceStore
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.core.runtime import RuntimePaths
from bai.execution.harness import Harness


class RejectAuditPolicy(PolicyEngine):
    def __init__(self, store: WorkspaceStore) -> None:
        super().__init__(store)
        self.calls: list[str] = []

    def gate_effect(
        self,
        *,
        workspace: WorkspaceRecord,
        effect: dict,
        approved: bool = False,
    ) -> PolicyDecision:
        self.calls.append(f"effect:{effect['kind']}")
        if effect["kind"] == "audit_write":
            return PolicyDecision(False, "rejected audit_write")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def test_audit_store_writes_json_under_runtime_state(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    audit_store = AuditStore(runtime, PolicyEngine(store))

    path = audit_store.write(
        workspace=workspace,
        task_id="task-1",
        audit_result={
            "status": "passed_with_notes",
            "findings": [
                {
                    "severity": "info",
                    "code": "no_test_command",
                    "message": "no trusted test command ran",
                }
            ],
        },
        source_artifacts={"context_artifact": "context.json"},
    )

    assert path.is_relative_to(runtime.state / "audits")
    audit = read_json(path)
    assert audit["audit_id"].startswith("audit-")
    assert audit["task_id"] == "task-1"
    assert audit["workspace_id"] == workspace.workspace_id
    assert audit["created_at"]
    assert audit["status"] == "passed_with_notes"
    assert audit["findings"][0]["code"] == "no_test_command"
    assert audit["source_artifacts"] == {"context_artifact": "context.json"}


def test_audit_store_write_is_policy_gated(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = RejectAuditPolicy(store)
    audit_store = AuditStore(runtime, policy)

    with pytest.raises(PermissionError, match="rejected audit_write"):
        audit_store.write(
            workspace=workspace,
            task_id="task-1",
            audit_result={"status": "passed", "findings": []},
            source_artifacts={},
        )

    assert policy.calls == ["effect:audit_write"]
    assert not (runtime.state / "audits").exists()


def test_policy_allows_audit_writes_only_under_runtime_audits(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = PolicyEngine(store)

    allowed = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "audit_write", "path": str(runtime.state / "audits" / "task.json")},
    )
    workspace_path = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "audit_write", "path": str(workspace_root / "audit.json")},
    )

    assert allowed.allowed is True
    assert workspace_path.allowed is False
    assert "runtime" in workspace_path.reason or "audits" in workspace_path.reason


def test_build_audit_result_notes_no_test_command() -> None:
    result = build_audit_result(
        agent_output={"proposed_effects": [], "proposed_changes": []},
        applied_changes=[],
        denied_changes=[],
        inspections=[],
        test_runs=[],
        workflow_status="completed",
    )

    assert result == {
        "status": "passed_with_notes",
        "findings": [
            {
                "severity": "info",
                "code": "no_test_command",
                "message": "no trusted test command ran",
            }
        ],
    }


def test_build_audit_result_warns_for_denied_mutation() -> None:
    result = build_audit_result(
        agent_output={"proposed_effects": [], "proposed_changes": []},
        applied_changes=[],
        denied_changes=[
            {
                "path": "/workspace/new.py",
                "operation": "create",
                "status": "denied",
                "reason": "mutation requires harness approval",
            }
        ],
        inspections=[],
        test_runs=[],
        workflow_status="completed_with_denials",
    )

    assert result["status"] == "warnings"
    assert {
        "severity": "warning",
        "code": "mutation_denied",
        "message": "code mutation proposal was denied",
        "path": "/workspace/new.py",
        "operation": "create",
        "reason": "mutation requires harness approval",
    } in result["findings"]


def test_build_audit_result_notes_trusted_test_command_without_sandbox() -> None:
    result = build_audit_result(
        agent_output={"proposed_effects": [], "proposed_changes": []},
        applied_changes=[],
        denied_changes=[],
        inspections=[],
        test_runs=[
            {
                "argv": ["python", "-m", "pytest"],
                "exit_code": 0,
                "write_enforcement": "trusted_command_no_sandbox",
            }
        ],
        workflow_status="completed",
    )

    assert result == {
        "status": "passed_with_notes",
        "findings": [
            {
                "severity": "info",
                "code": "trusted_test_command_no_sandbox",
                "message": "test command ran as trusted direct execution without a write sandbox",
            }
        ],
    }


def test_build_audit_result_errors_for_failed_test_run() -> None:
    result = build_audit_result(
        agent_output={"proposed_effects": [], "proposed_changes": []},
        applied_changes=[],
        denied_changes=[],
        inspections=[],
        test_runs=[
            {
                "argv": ["python", "-c", "raise SystemExit(3)"],
                "exit_code": 3,
                "write_enforcement": "trusted_command_no_sandbox",
            }
        ],
        workflow_status="failed",
    )

    assert result["status"] == "failed"
    assert {
        "severity": "error",
        "code": "test_command_failed",
        "message": "test command failed with exit code 3",
        "exit_code": 3,
    } in result["findings"]


def test_dev_workflow_success_writes_audit_artifact_and_binds_workflow(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="dev plan only",
    )

    audit = read_json(result["audit_artifact"])
    workflow = read_json(result["workflow_artifact"])
    working_memory = read_json(result["working_memory_artifact"])
    audit_source_events = [
        read_json(path)["event_type"] for path in audit["source_artifacts"]["event_artifacts"]
    ]
    assert Path(result["audit_artifact"]).is_relative_to(runtime.state / "audits")
    assert result["audit_findings"] == audit["findings"]
    assert result["node_results"]["audit"]["status"] == audit["status"]
    assert audit_source_events == ["started", "completed"]
    assert audit["source_artifacts"]["workflow_artifact"] == result["workflow_artifact"]
    assert workflow["artifacts"]["audit_artifact"] == result["audit_artifact"]
    assert workflow["artifacts"]["audit_findings"] == audit["findings"]
    assert working_memory["content"]["audit_artifact"] == result["audit_artifact"]
    assert not (runtime.state / "audits").is_relative_to(workspace_root)


def test_default_workflow_does_not_write_audit_artifact(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="plan only",
    )

    assert result["audit_artifact"] is None
    assert not (runtime.state / "audits").exists()


def test_dev_audit_write_failure_leaves_no_memory_artifacts(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = RejectAuditPolicy(store)

    with pytest.raises(PermissionError, match="rejected audit_write"):
        Harness(runtime=runtime, workspaces=store, policy=policy).run(
            workspace_ref=workspace.workspace_id,
            request="dev plan only",
        )

    assert not (runtime.memory / workspace.workspace_id).exists()


class RejectTerminalWorkflowPolicy(PolicyEngine):
    def __init__(self, store: WorkspaceStore) -> None:
        super().__init__(store)
        self.workflow_writes = 0

    def gate_effect(
        self,
        *,
        workspace: WorkspaceRecord,
        effect: dict,
        approved: bool = False,
    ) -> PolicyDecision:
        if effect["kind"] == "workflow_write":
            self.workflow_writes += 1
            if self.workflow_writes == 2:
                return PolicyDecision(False, "rejected terminal workflow_write")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


def test_terminal_workflow_write_failure_leaves_no_memory_artifacts(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = RejectTerminalWorkflowPolicy(store)

    with pytest.raises(PermissionError, match="rejected terminal workflow_write"):
        Harness(runtime=runtime, workspaces=store, policy=policy).run(
            workspace_ref=workspace.workspace_id,
            request="plan only",
        )

    assert not (runtime.memory / workspace.workspace_id).exists()


class CountingWorkflowStore(WorkflowStore):
    def __init__(self, runtime: RuntimePaths, policy: PolicyEngine) -> None:
        super().__init__(runtime, policy)
        self.statuses: list[str] = []

    def write(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        workflow: dict,
        checked_path: Path | None = None,
    ) -> Path:
        self.statuses.append(workflow["status"])
        return super().write(
            workspace=workspace,
            task_id=task_id,
            workflow=workflow,
            checked_path=checked_path,
        )


def test_success_finalization_writes_one_terminal_workflow_artifact(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    workflows = CountingWorkflowStore(runtime, PolicyEngine(store))

    result = Harness(runtime=runtime, workspaces=store, workflows=workflows).run(
        workspace_ref=workspace.workspace_id,
        request="plan only",
    )

    workflow = read_json(result["workflow_artifact"])
    assert workflows.statuses == ["planned", "completed"]
    assert workflow["artifacts"]["memory_artifacts"] == result["memory_artifacts"]


class FailingFinalWorkflowStore(WorkflowStore):
    def __init__(self, runtime: RuntimePaths, policy: PolicyEngine) -> None:
        super().__init__(runtime, policy)
        self.write_count = 0

    def write(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        workflow: dict,
        checked_path: Path | None = None,
    ) -> Path:
        self.write_count += 1
        if self.write_count == 2:
            raise OSError("final workflow write failed")
        return super().write(
            workspace=workspace,
            task_id=task_id,
            workflow=workflow,
            checked_path=checked_path,
        )


def test_final_workflow_write_failure_cleans_up_memory_artifacts(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    workflows = FailingFinalWorkflowStore(runtime, PolicyEngine(store))

    with pytest.raises(OSError, match="final workflow write failed"):
        Harness(runtime=runtime, workspaces=store, workflows=workflows).run(
            workspace_ref=workspace.workspace_id,
            request="plan only",
        )

    assert not (runtime.memory / workspace.workspace_id).exists()
