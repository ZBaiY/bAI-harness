from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.artifacts.context import ContextStore
from bai.execution.harness import Harness
from bai.artifacts.memory import MemoryStore
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.core.runtime import RuntimePaths
from bai.artifacts.workflow import WorkflowStore
from bai.config.workspace import WorkspaceRecord, WorkspaceStore


class RejectArtifactPolicy(PolicyEngine):
    def __init__(self, store: WorkspaceStore, reject_kind: str) -> None:
        super().__init__(store)
        self.reject_kind = reject_kind

    def gate_effect(
        self,
        *,
        workspace: WorkspaceRecord,
        effect: dict,
        approved: bool = False,
    ) -> PolicyDecision:
        if effect["kind"] == self.reject_kind:
            return PolicyDecision(False, f"rejected {self.reject_kind}")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def test_workflow_store_writes_serial_dag_under_runtime_state(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    workflow_store = WorkflowStore(runtime, PolicyEngine(store))

    path = workflow_store.write(
        workspace=workspace,
        task_id="task-1",
        workflow={
            "execution_policy": "serial",
            "nodes": [{"id": "plan", "kind": "plan", "depends_on": []}],
            "edges": [],
            "artifacts": {},
            "checkpoints": [],
            "status": "planned",
        },
    )

    assert path.is_relative_to(runtime.state / "workflows")
    workflow = read_json(path)
    assert workflow["task_id"] == "task-1"
    assert workflow["workspace_id"] == workspace.workspace_id
    assert workflow["execution_policy"] == "serial"
    assert workflow["nodes"] == [{"id": "plan", "kind": "plan", "depends_on": []}]
    assert workflow["edges"] == []


def test_workflow_write_is_policy_gated(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    workflow_store = WorkflowStore(runtime, RejectArtifactPolicy(store, "workflow_write"))

    with pytest.raises(PermissionError, match="rejected workflow_write"):
        workflow_store.write(
            workspace=workspace,
            task_id="task-1",
            workflow={
                "execution_policy": "serial",
                "nodes": [],
                "edges": [],
                "artifacts": {},
                "checkpoints": [],
                "status": "planned",
            },
        )

    assert not (runtime.state / "workflows").exists()


def test_context_store_writes_scoped_bundle_without_workspace_crawling(
    runtime: RuntimePaths,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    secret = workspace_root / "secret.txt"
    secret.write_text("do not crawl me\n")
    original_read_text = Path.read_text

    def fail_if_workspace_file_read(path: Path, *args, **kwargs) -> str:
        if path == secret:
            raise AssertionError("context bundle must not crawl workspace files")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_if_workspace_file_read)
    context_store = ContextStore(runtime, PolicyEngine(store))

    path = context_store.write(
        workspace=workspace,
        task_id="task-1",
        bundle={
            "user_request": "plan only",
            "execution_policy": "serial",
            "router_constraints": {"local": True, "workspace_network": False},
            "approved_memory_snippets": [],
        },
    )

    context = read_json(path)
    assert path.is_relative_to(runtime.state / "context")
    assert context["workspace_id"] == workspace.workspace_id
    assert context["user_request"] == "plan only"
    assert context["workspace"] == {
        "workspace_id": workspace.workspace_id,
        "name": workspace.name,
        "root_path": workspace.root_path,
        "allowed_paths": workspace.allowed_paths,
        "memory_scope": workspace.memory_scope,
    }
    assert context["approved_memory_snippets"] == []
    assert "do not crawl me" not in json.dumps(context)
    assert "BAI_HOME" not in json.dumps(context)


def test_context_write_is_policy_gated(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    context_store = ContextStore(runtime, RejectArtifactPolicy(store, "context_write"))

    with pytest.raises(PermissionError, match="rejected context_write"):
        context_store.write(
            workspace=workspace,
            task_id="task-1",
            bundle={
                "user_request": "plan only",
                "execution_policy": "serial",
                "router_constraints": {},
                "approved_memory_snippets": [],
            },
        )

    assert not (runtime.state / "context").exists()


def test_policy_allows_workflow_and_context_only_under_runtime_state(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = PolicyEngine(store)

    allowed_workflow = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "workflow_write",
            "path": str(runtime.state / "workflows" / "task-1.json"),
        },
    )
    allowed_context = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "context_write",
            "path": str(runtime.state / "context" / "task-1.json"),
        },
    )
    workspace_workflow = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "workflow_write", "path": str(workspace_root / "workflow.json")},
    )
    workspace_context = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "context_write", "path": str(workspace_root / "context.json")},
    )

    assert allowed_workflow.allowed is True
    assert allowed_context.allowed is True
    assert workspace_workflow.allowed is False
    assert workspace_context.allowed is False


def test_harness_workflow_dag_invariants_for_plan_only(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="plan only",
    )

    workflow = read_json(result["workflow_artifact"])
    assert workflow["execution_policy"] == "serial"
    assert [node["id"] for node in workflow["nodes"]] == ["plan", "memory_write"]
    assert workflow["edges"] == [{"from": "plan", "to": "memory_write"}]
    assert all(node["kind"] != "parallel_worker" for node in workflow["nodes"])
    assert all(node["kind"] != "agent_to_agent" for node in workflow["nodes"])
    assert workflow["artifacts"]["approval_artifacts"] == result["approval_artifacts"]
    assert workflow["artifacts"]["memory_artifacts"] == result["memory_artifacts"]


def test_harness_workflow_inspect_node_is_read_only(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="inspect README.md",
    )

    workflow = read_json(result["workflow_artifact"])
    inspect_nodes = [node for node in workflow["nodes"] if node["kind"] == "inspect"]
    assert len(inspect_nodes) == 1
    assert inspect_nodes[0]["can_mutate"] is False
    assert inspect_nodes[0]["effect"]["kind"] == "inspect_file"


def test_harness_workflow_mutation_node_requires_mutation_approval(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"

    denied_result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"propose-create {target}",
    )
    denied_workflow = read_json(denied_result["workflow_artifact"])
    assert [node["kind"] for node in denied_workflow["nodes"]] == ["plan", "memory_write"]

    approved_result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"propose-create {target}",
        approved_mutation_paths=[str(target)],
    )
    approved_workflow = read_json(approved_result["workflow_artifact"])
    mutation_nodes = [
        node for node in approved_workflow["nodes"] if node["kind"] == "code_mutation"
    ]
    assert len(mutation_nodes) == 1
    assert mutation_nodes[0]["approval_artifact"] in approved_result["approval_artifacts"]


def test_working_memory_records_current_workflow_decisions(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"propose-create {target}",
    )

    working_memory = read_json(result["working_memory_artifact"])
    content = working_memory["content"]
    assert working_memory["kind"] == "working"
    assert content["plan_id"] == result["plan"]["id"]
    assert content["task_id"] == result["task_id"]
    assert content["workspace_id"] == workspace.workspace_id
    assert content["risks"] == result["plan"]["risks"]
    assert content["requested_approval_scope"] == result["plan"]["requested_approval_scope"]
    assert result["working_memory_artifact"] in result["memory_artifacts"]


def test_working_memory_write_is_policy_gated(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    memory = MemoryStore(runtime, RejectArtifactPolicy(store, "memory_write"))

    with pytest.raises(PermissionError, match="rejected memory_write"):
        memory.write(
            workspace=workspace,
            kind="working",
            content={"task_id": "task-1"},
        )

    assert not (runtime.memory / workspace.workspace_id / "working").exists()
