from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.execution.scheduler import Scheduler
from bai.config.workspace import WorkspaceRecord, WorkspaceStore
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.core.runtime import RuntimePaths


class RejectSchedulerPolicy(PolicyEngine):
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
        if effect["kind"] == "scheduler_write":
            return PolicyDecision(False, "rejected scheduler_write")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def test_scheduler_no_longer_exposes_toy_task_api() -> None:
    scheduler = Scheduler()

    assert not hasattr(scheduler, "start_toy_task")
    assert not hasattr(scheduler, "request_pause")
    assert not hasattr(scheduler, "pause")
    assert scheduler.foreground_priority == "foreground_over_background"


def test_scheduler_records_background_deferred_artifact_under_runtime_state(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    scheduler = Scheduler(runtime=runtime, workspaces=store, policy=PolicyEngine(store))

    path = scheduler.defer_background(
        workspace=workspace,
        request="dev plan only",
        execution_policy="serial",
        reason="background requested",
    )

    assert path.is_relative_to(runtime.state / "scheduler")
    assert not path.is_relative_to(workspace_root)
    record = read_json(path)
    assert record["task_id"].startswith("scheduler-")
    assert record["workspace_id"] == workspace.workspace_id
    assert record["request"] == "dev plan only"
    assert record["priority"] == "background"
    assert record["lifecycle_status"] == "deferred"
    assert record["execution_state"] == "deferred_metadata_only"
    assert record["preemptible"] is True
    assert "executed" not in record
    assert record["created_at"]
    assert record["reason"] == "background requested"


def test_scheduler_write_is_policy_gated(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = RejectSchedulerPolicy(store)
    scheduler = Scheduler(runtime=runtime, workspaces=store, policy=policy)

    with pytest.raises(PermissionError, match="rejected scheduler_write"):
        scheduler.defer_background(
            workspace=workspace,
            request="dev plan only",
            execution_policy="serial",
            reason="background requested",
        )

    assert policy.calls == ["effect:scheduler_write"]
    assert not (runtime.state / "scheduler").exists()


def test_policy_allows_scheduler_writes_only_under_runtime_scheduler_state(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = PolicyEngine(store)

    allowed = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "scheduler_write",
            "path": str(runtime.state / "scheduler" / "task.json"),
        },
    )
    workspace_path = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "scheduler_write", "path": str(workspace_root / "task.json")},
    )

    assert allowed.allowed is True
    assert workspace_path.allowed is False
    assert "runtime" in workspace_path.reason or "scheduler" in workspace_path.reason


def test_scheduler_rejects_non_serial_execution_policy(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    scheduler = Scheduler(runtime=runtime, workspaces=store, policy=PolicyEngine(store))

    with pytest.raises(ValueError, match="scheduler accepts only serial execution"):
        scheduler.defer_background(
            workspace=workspace,
            request="dev plan only",
            execution_policy="explicit_parallel",
            reason="background requested",
        )

    assert not (runtime.state / "scheduler").exists()


def test_foreground_admission_records_priority_over_planned_background(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    scheduler = Scheduler(runtime=runtime, workspaces=store, policy=PolicyEngine(store))
    background_path = scheduler.defer_background(
        workspace=workspace,
        request="dev plan only",
        execution_policy="serial",
        reason="background requested",
    )

    foreground_path = scheduler.admit_foreground(
        workspace=workspace,
        request="dev inspect README.md",
        execution_policy="serial",
        reason="foreground requested",
    )

    background = read_json(background_path)
    foreground = read_json(foreground_path)
    assert background["lifecycle_status"] == "preemptible_deferred"
    assert background["preemptible"] is True
    assert foreground["priority"] == "foreground"
    assert foreground["lifecycle_status"] == "accepted"
    assert foreground["execution_state"] == "accepted_for_foreground_run"
    assert "executed" not in foreground
    assert foreground["preemptible"] is False
    assert foreground["preempts_background"] is True
    assert foreground["preempted_background_task_ids"] == [background["task_id"]]
