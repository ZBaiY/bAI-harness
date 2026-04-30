from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from bai.core.errors import BaiUserError
from bai.execution.harness import Harness
from bai.core.runtime import RuntimePaths
from bai.config.workspace import WorkspaceStore


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def test_dev_workflow_artifact_contains_serial_template_nodes(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="dev plan only",
    )

    workflow = read_json(result["workflow_artifact"])
    assert result["workflow_mode"] == "dev"
    assert workflow["workflow_mode"] == "dev"
    assert workflow["execution_policy"] == "serial"
    assert [node["id"] for node in workflow["nodes"]] == [
        "plan",
        "code",
        "doc",
        "test",
        "audit",
        "fix",
    ]
    assert workflow["edges"] == [
        {"from": "plan", "to": "code"},
        {"from": "code", "to": "doc"},
        {"from": "code", "to": "test"},
        {"from": "doc", "to": "audit"},
        {"from": "test", "to": "audit"},
        {"from": "audit", "to": "fix"},
    ]
    assert all(node["kind"] != "parallel_worker" for node in workflow["nodes"])
    assert all(node["kind"] != "agent_to_agent" for node in workflow["nodes"])


def test_dev_workflow_node_semantics_without_effects(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="dev plan only",
    )

    nodes = read_json(result["workflow_artifact"])["nodes"]
    by_id = {node["id"]: node for node in nodes}
    node_results = result["node_results"]
    assert by_id["plan"]["can_mutate"] is False
    assert by_id["code"]["approval_required"] is True
    assert by_id["doc"]["can_mutate"] is False
    assert by_id["test"]["allowed_tools"] == ["test_command"]
    assert by_id["audit"]["can_mutate"] is False
    assert by_id["fix"]["can_mutate"] is False
    assert node_results["plan"]["status"] == "completed"
    assert node_results["code"]["status"] == "skipped"
    assert node_results["doc"]["status"] == "proposal_only"
    assert node_results["test"]["status"] == "skipped"
    assert node_results["audit"]["status"] == "completed"
    assert node_results["fix"]["status"] == "skipped"
    assert result["audit_findings"] == []
    assert result["fix_proposals"] == []


def test_dev_workflow_missing_mutation_approval_is_explicit_denial(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"dev propose-create {target}",
    )

    assert result["status"] == "completed_with_denials"
    assert result["workflow_mode"] == "dev"
    assert result["applied_changes"] == []
    assert result["denied_changes"][0]["path"] == str(target.resolve())
    assert not target.exists()
    assert result["node_results"]["code"]["status"] == "denied"
    assert result["node_results"]["fix"]["status"] == "proposal_only"
    assert result["fix_proposals"] == [
        {
            "status": "proposal_only",
            "reason": "manual intervention required for audit findings",
        }
    ]


def test_dev_workflow_approved_mutation_runs_before_test_and_audit(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"
    argv = [
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('created.md').exists(); print('tested')",
    ]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"dev propose-create {target} test {shlex.join(argv)}",
        approved_mutation_paths=[str(target)],
    )

    assert target.exists()
    assert result["applied_changes"] == [
        {"path": str(target.resolve()), "operation": "create"}
    ]
    assert result["test_runs"][0]["stdout_preview"] == "tested\n"
    assert result["node_results"]["code"]["status"] == "completed"
    assert result["node_results"]["test"]["status"] == "completed"
    assert result["node_results"]["audit"]["status"] == "completed"
    assert result["node_results"]["fix"]["status"] == "skipped"
    assert result["audit_findings"] == []


def test_dev_workflow_failed_test_records_failed_node_without_memory_or_fix(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [sys.executable, "-c", "import sys; sys.exit(4)"]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])

    with pytest.raises(BaiUserError, match="test command failed with exit code 4"):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request=f"dev test {shlex.join(argv)}",
        )

    events = [
        (path, read_json(path)["event_type"])
        for path in sorted((runtime.state / "events").glob("*/*.json"))
    ]
    workflow = read_json(next((runtime.state / "workflows").glob("*.json")))
    node_results = workflow["artifacts"]["node_results"]
    assert [event_type for _, event_type in events] == ["started", "failed"]
    assert workflow["artifacts"]["event_artifacts"] == [
        str(path) for path, _ in events
    ]
    assert workflow["status"] == "failed"
    assert node_results["test"]["status"] == "failed"
    assert node_results["fix"]["status"] == "skipped"
    assert workflow["artifacts"]["fix_proposals"] == []
    assert not (runtime.memory / workspace.workspace_id).exists()
