from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from bai.artifacts.fix import FixStore, build_fix_proposals
from bai.config.workspace import WorkspaceRecord, WorkspaceStore
from bai.core.errors import BaiUserError
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.core.runtime import RuntimePaths
from bai.execution.harness import Harness


class RejectFixPolicy(PolicyEngine):
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
        if effect["kind"] == "fix_write":
            return PolicyDecision(False, "rejected fix_write")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def test_fix_store_writes_json_under_runtime_state(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    fix_store = FixStore(runtime, PolicyEngine(store))

    path = fix_store.write(
        workspace=workspace,
        task_id="task-1",
        proposals=[
            {
                "status": "proposal_only",
                "kind": "request_mutation_approval",
                "message": "request approval for denied mutation path",
                "path": str(workspace_root / "new.py"),
                "operation": "create",
                "source_finding_code": "mutation_denied",
                "auto_apply": False,
            }
        ],
        source_audit_artifact=str(runtime.state / "audits" / "task-1.json"),
    )

    assert path.is_relative_to(runtime.state / "fixes")
    fix = read_json(path)
    assert fix["fix_id"].startswith("fix-")
    assert fix["task_id"] == "task-1"
    assert fix["workspace_id"] == workspace.workspace_id
    assert fix["created_at"]
    assert fix["status"] == "proposal_only"
    assert fix["source_audit_artifact"] == str(runtime.state / "audits" / "task-1.json")
    assert fix["proposals"][0]["source_finding_code"] == "mutation_denied"
    assert "patch" not in json.dumps(fix)
    assert "command" not in json.dumps(fix)


def test_fix_store_write_is_policy_gated(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = RejectFixPolicy(store)
    fix_store = FixStore(runtime, policy)

    with pytest.raises(PermissionError, match="rejected fix_write"):
        fix_store.write(
            workspace=workspace,
            task_id="task-1",
            proposals=[],
            source_audit_artifact=str(runtime.state / "audits" / "task-1.json"),
        )

    assert policy.calls == ["effect:fix_write"]
    assert not (runtime.state / "fixes").exists()


def test_policy_allows_fix_writes_only_under_runtime_fixes(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = PolicyEngine(store)

    allowed = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "fix_write", "path": str(runtime.state / "fixes" / "task.json")},
    )
    workspace_path = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "fix_write", "path": str(workspace_root / "fix.json")},
    )

    assert allowed.allowed is True
    assert workspace_path.allowed is False
    assert "runtime" in workspace_path.reason or "fixes" in workspace_path.reason


def test_build_fix_proposals_for_denied_mutation() -> None:
    proposals = build_fix_proposals(
        [
            {
                "severity": "warning",
                "code": "mutation_denied",
                "path": "/workspace/new.py",
                "operation": "create",
            }
        ]
    )

    assert proposals == [
        {
            "status": "proposal_only",
            "kind": "request_mutation_approval",
            "message": "request approval for denied mutation path",
            "path": "/workspace/new.py",
            "operation": "create",
            "source_finding_code": "mutation_denied",
            "auto_apply": False,
        }
    ]


def test_build_fix_proposals_for_failed_test_command() -> None:
    proposals = build_fix_proposals(
        [
            {
                "severity": "error",
                "code": "test_command_failed",
                "message": "test command failed with exit code 3",
            }
        ]
    )

    assert proposals == [
        {
            "status": "proposal_only",
            "kind": "manual_test_failure_review",
            "message": "inspect failing test output and update code manually",
            "source_finding_code": "test_command_failed",
            "auto_apply": False,
        }
    ]


@pytest.mark.parametrize(
    "code",
    ["no_test_command", "trusted_test_command_no_sandbox"],
)
def test_build_fix_proposals_ignores_info_only_findings(code: str) -> None:
    proposals = build_fix_proposals(
        [{"severity": "info", "code": code, "message": "informational"}]
    )

    assert proposals == []


def test_dev_workflow_denied_mutation_writes_fix_artifact(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"dev propose-create {target}",
    )

    fix = read_json(result["fix_artifact"])
    workflow = read_json(result["workflow_artifact"])
    working_memory = read_json(result["working_memory_artifact"])
    assert result["status"] == "completed_with_denials"
    assert not target.exists()
    assert fix["source_audit_artifact"] == result["audit_artifact"]
    assert fix["proposals"] == result["fix_proposals"]
    assert fix["proposals"][0]["path"] == str(target.resolve())
    assert workflow["artifacts"]["fix_artifact"] == result["fix_artifact"]
    assert result["node_results"]["fix"]["artifact"] == result["fix_artifact"]
    assert result["node_results"]["fix"]["auto_apply"] is False
    assert working_memory["content"]["fix_artifact"] == result["fix_artifact"]


def test_dev_workflow_info_only_audit_does_not_write_fix_artifact(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="dev plan only",
    )

    assert result["fix_artifact"] is None
    assert result["fix_proposals"] == []
    assert not (runtime.state / "fixes").exists()


def test_failed_dev_test_writes_fix_artifact_without_memory_or_mutation(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [sys.executable, "-c", "import sys; sys.exit(5)"]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])

    with pytest.raises(BaiUserError, match="test command failed with exit code 5"):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request=f"dev test {shlex.join(argv)}",
        )

    workflow = read_json(next((runtime.state / "workflows").glob("*.json")))
    fix = read_json(workflow["artifacts"]["fix_artifact"])
    assert workflow["status"] == "failed"
    assert fix["source_audit_artifact"] == workflow["artifacts"]["audit_artifact"]
    assert fix["proposals"][0]["source_finding_code"] == "test_command_failed"
    assert workflow["artifacts"]["node_results"]["fix"]["auto_apply"] is False
    assert not (runtime.memory / workspace.workspace_id).exists()
