from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bai.execution.agent import PlanAgent
from bai.execution.harness import INSPECTION_PREVIEW_CHARS, Harness
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.config.router import ModelRoute
from bai.core.runtime import RuntimePaths
from bai.config.workspace import WorkspaceRecord, WorkspaceStore


class RecordingRejectInspectPolicy(PolicyEngine):
    def __init__(self, store: WorkspaceStore) -> None:
        super().__init__(store)
        self.calls: list[str] = []

    def gate_agent_output(self, output: dict) -> PolicyDecision:
        self.calls.append("agent_output")
        return super().gate_agent_output(output)

    def gate_effect(self, *, workspace, effect, approved=False) -> PolicyDecision:
        self.calls.append(f"effect:{effect['kind']}")
        if effect["kind"] == "inspect_file":
            return PolicyDecision(False, "rejected inspect_file")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


def assert_calls_in_order(calls: list[str], expected: list[str]) -> None:
    cursor = 0
    for call in expected:
        assert call in calls[cursor:]
        cursor = calls.index(call, cursor) + 1


def test_plan_agent_proposes_inspect_effect_only_for_explicit_inspect(
    workspace_root: Path,
) -> None:
    workspace = WorkspaceRecord(
        workspace_id="demo-id",
        name="demo",
        root_path=str(workspace_root),
        allowed_paths=[str(workspace_root)],
        default_branch=None,
        command_policy={"inspect": {"allowed": True}},
        network_policy={"workspace_network": False},
        sandbox_path=str(workspace_root / ".sandbox"),
        memory_scope="workspace:demo-id",
    )
    route = ModelRoute(provider="local", model="local-plan-stub", endpoint="stub://local/plan")
    agent = PlanAgent()

    inspect_output = agent.run(
        request="inspect README.md",
        workspace=workspace,
        execution_policy="serial",
        model_route=route,
    )
    plan_output = agent.run(
        request="plan only",
        workspace=workspace,
        execution_policy="serial",
        model_route=route,
    )

    assert inspect_output["proposed_effects"] == [
        {"kind": "inspect_file", "path": "README.md"}
    ]
    assert plan_output["proposed_effects"] == []


def test_policy_allows_inspect_file_without_mutation_approval(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = PolicyEngine(store)

    decision = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "inspect_file", "path": str(workspace_root / "README.md")},
        approved=False,
    )

    assert decision.allowed is True


def test_unknown_effect_kind_remains_rejected(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    decision = PolicyEngine(store).gate_effect(
        workspace=workspace,
        effect={"kind": "surprise_tool", "path": str(workspace_root / "README.md")},
    )

    assert decision.allowed is False
    assert "unknown effect kind" in decision.reason


def test_workspace_default_command_policy_allows_only_inspect(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    workspace = WorkspaceStore(runtime).add("demo", workspace_root)

    assert workspace.command_policy["inspect"]["allowed"] is True
    for command_class in ["test", "mutate", "workspace_network", "destructive"]:
        assert workspace.command_policy[command_class]["allowed"] is False


def test_inspection_denied_when_workspace_inspect_policy_disabled(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    disabled = replace(
        workspace,
        command_policy={**workspace.command_policy, "inspect": {"allowed": False}},
    )

    decision = PolicyEngine(store).gate_effect(
        workspace=disabled,
        effect={"kind": "inspect_file", "path": str(workspace_root / "README.md")},
    )

    assert decision.allowed is False
    assert "inspect command policy" in decision.reason


def test_task_scope_cannot_broaden_disabled_workspace_inspect_policy(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    disabled = replace(
        workspace,
        command_policy={**workspace.command_policy, "inspect": {"allowed": False}},
    )

    decision = PolicyEngine(store).gate_effect(
        workspace=disabled,
        effect={
            "kind": "inspect_file",
            "path": str(workspace_root / "README.md"),
            "task_scope": {"command_policy": {"inspect": {"allowed": True}}},
        },
    )

    assert decision.allowed is False
    assert "inspect command policy" in decision.reason


def test_harness_inspection_rejects_before_file_read_and_memory_write(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = RecordingRejectInspectPolicy(store)

    with pytest.raises(PermissionError, match="rejected inspect_file"):
        Harness(runtime=runtime, workspaces=store, policy=policy).run(
            workspace_ref=workspace.workspace_id,
            request="inspect README.md",
        )

    assert_calls_in_order(
        policy.calls,
        [
            "agent_output",
            "effect:approval_write",
            "effect:inspect_file",
            "effect:task_event_write",
        ],
    )
    assert "effect:memory_write" not in policy.calls
    assert not (runtime.memory / workspace.workspace_id).exists()


def test_harness_inspection_preview_does_not_use_unbounded_read_text(
    runtime: RuntimePaths,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "large.txt"
    target.write_text(("x" * INSPECTION_PREVIEW_CHARS) + "sentinel")
    original_read_text = Path.read_text

    def fail_if_inspection_read_text(path: Path, *args, **kwargs) -> str:
        if path == target:
            raise AssertionError("inspection must not read the entire file")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_if_inspection_read_text)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="inspect large.txt",
    )

    inspection = result["inspections"][0]
    assert inspection["content_preview"] == "x" * INSPECTION_PREVIEW_CHARS
    assert inspection["truncated"] is True
