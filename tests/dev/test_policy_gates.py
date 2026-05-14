from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bai.execution.harness import Harness
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.config.router import ModelRoute
from bai.core.runtime import RuntimePaths
from bai.config.workspace import WorkspaceRecord, WorkspaceStore


@dataclass(frozen=True)
class NonSerialPlanAgent:
    def run(
        self,
        *,
        request: str,
        workspace: WorkspaceRecord,
        execution_policy: str,
        model_route: ModelRoute,
    ) -> dict[str, Any]:
        return {
            "id": "plan-explicit-parallel",
            "agent_kind": "plan",
            "workspace_id": workspace.workspace_id,
            "execution_policy": "explicit_parallel",
            "model": {
                "provider": model_route.provider,
                "model": model_route.model,
                "endpoint": model_route.endpoint,
            },
            "summary": request,
            "ordered_steps": [],
            "risks": [],
            "requested_approval_scope": [],
            "proposed_changes": [],
        }


class SelectiveRejectPolicy(PolicyEngine):
    def __init__(self, store: WorkspaceStore, reject_effect: str | None = None) -> None:
        super().__init__(store)
        self.reject_effect = reject_effect
        self.calls: list[str] = []

    def gate_agent_output(self, output: dict[str, Any]) -> PolicyDecision:
        self.calls.append("agent_output")
        return super().gate_agent_output(output)

    def gate_effect(
        self,
        *,
        workspace: WorkspaceRecord,
        effect: dict[str, Any],
        approved: bool = False,
    ) -> PolicyDecision:
        self.calls.append(f"effect:{effect['kind']}")
        if effect["kind"] == self.reject_effect:
            return PolicyDecision(False, f"rejected {self.reject_effect}")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


def assert_calls_in_order(calls: list[str], expected: list[str]) -> None:
    cursor = 0
    for call in expected:
        assert call in calls[cursor:]
        cursor = calls.index(call, cursor) + 1


def test_policy_rejects_explicit_parallel_agent_output_before_approval_memory_or_mutation(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)

    with pytest.raises(PermissionError):
        Harness(runtime=runtime, workspaces=store, agent=NonSerialPlanAgent()).run(
            workspace_ref=record.workspace_id,
            request="plan in parallel",
        )

    assert not (runtime.state / "approvals").exists()
    assert not (runtime.memory / record.workspace_id).exists()
    assert list(workspace_root.iterdir()) == [workspace_root / "README.md"]


def test_policy_rejects_delete_mutation_proposals_before_effect_execution(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    policy = PolicyEngine(store)

    decision = policy.gate_agent_output(
        {
            "id": "plan-delete",
            "agent_kind": "plan",
            "workspace_id": record.workspace_id,
            "execution_policy": "serial",
            "proposed_changes": [
                {
                    "path": str(workspace_root / "README.md"),
                    "operation": "delete",
                    "requires_approval": True,
                }
            ],
            "proposed_effects": [],
        }
    )

    assert decision.allowed is False
    assert decision.reason == "invalid proposed change operation"


def test_policy_gate_blocks_approval_artifact_before_write(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    policy = SelectiveRejectPolicy(store, "approval_write")

    with pytest.raises(PermissionError, match="rejected approval_write"):
        Harness(runtime=runtime, workspaces=store, policy=policy).run(
            workspace_ref=record.workspace_id,
            request="plan only",
        )

    assert_calls_in_order(
        policy.calls,
        ["agent_output", "effect:approval_write", "effect:task_event_write"],
    )
    assert "effect:memory_write" not in policy.calls
    assert "effect:code_mutation" not in policy.calls
    assert not (runtime.state / "approvals").exists()


def test_policy_gate_blocks_memory_artifact_before_write(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    policy = SelectiveRejectPolicy(store, "memory_write")

    with pytest.raises(PermissionError, match="rejected memory_write"):
        Harness(runtime=runtime, workspaces=store, policy=policy).run(
            workspace_ref=record.workspace_id,
            request="plan only",
        )

    assert_calls_in_order(
        policy.calls,
        [
            "agent_output",
            "effect:approval_write",
            "effect:memory_write",
            "effect:task_event_write",
        ],
    )
    assert "effect:code_mutation" not in policy.calls
    assert (runtime.state / "approvals").exists()
    assert not (runtime.memory / record.workspace_id).exists()


def test_policy_gate_blocks_approved_mutation_before_write(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    target = workspace_root / "created.md"
    policy = SelectiveRejectPolicy(store, "code_mutation")

    with pytest.raises(PermissionError, match="rejected code_mutation"):
        Harness(runtime=runtime, workspaces=store, policy=policy).run(
            workspace_ref=record.workspace_id,
            request=f"propose-create {target}",
            approved_mutation_paths=[str(target)],
        )

    assert_calls_in_order(
        policy.calls,
        [
            "agent_output",
            "effect:approval_write",
            "effect:code_mutation",
            "effect:task_event_write",
        ],
    )
    assert "effect:memory_write" not in policy.calls
    assert not target.exists()


def test_policy_gate_runs_for_unapproved_mutation_proposal(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    target = workspace_root / "created.md"
    policy = SelectiveRejectPolicy(store)

    result = Harness(runtime=runtime, workspaces=store, policy=policy).run(
        workspace_ref=record.workspace_id,
        request=f"propose-create {target}",
    )

    assert result["applied_changes"] == []
    assert_calls_in_order(
        policy.calls,
        [
            "agent_output",
            "effect:approval_write",
            "effect:code_mutation",
            "effect:task_event_write",
            "effect:workflow_write",
            "effect:memory_write",
        ],
    )
    assert not target.exists()
