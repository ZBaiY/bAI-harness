from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.core.errors import BaiUserError
from bai.execution.harness import Harness
from bai.config.router import ModelRoute
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.core.runtime import RuntimePaths
from bai.artifacts.task_events import TaskEventStore
from bai.config.workspace import WorkspaceRecord, WorkspaceStore


class RejectEventPolicy(PolicyEngine):
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
        if effect["kind"] == "task_event_write":
            return PolicyDecision(False, "rejected task_event_write")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


class FailFailedEventStore(TaskEventStore):
    def write(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        event_type: str,
        reason: str,
        checkpoint_ref: str | None = None,
    ) -> Path:
        if event_type == "failed":
            raise RuntimeError("failed event write failed")
        return super().write(
            workspace=workspace,
            task_id=task_id,
            event_type=event_type,
            reason=reason,
            checkpoint_ref=checkpoint_ref,
        )


class BuggyAgent:
    def run(
        self,
        *,
        request: str,
        workspace: WorkspaceRecord,
        execution_policy: str,
        model_route: ModelRoute,
    ) -> dict:
        raise AssertionError("agent invariant exploded")


class ValueErrorBuggyAgent:
    def run(
        self,
        *,
        request: str,
        workspace: WorkspaceRecord,
        execution_policy: str,
        model_route: ModelRoute,
    ) -> dict:
        raise ValueError("internal value invariant exploded")


def read_event(path: Path) -> dict:
    return json.loads(path.read_text())


def test_task_event_store_writes_json_under_runtime_state(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    event_store = TaskEventStore(runtime, PolicyEngine(store))

    path = event_store.write(
        workspace=workspace,
        task_id="task-1",
        event_type="started",
        reason="run started",
        checkpoint_ref=None,
    )

    assert path.is_relative_to(runtime.state / "events")
    assert path.exists()
    event = read_event(path)
    assert event["task_id"] == "task-1"
    assert event["event_type"] == "started"
    assert event["workspace_id"] == workspace.workspace_id
    assert event["reason"] == "run started"
    assert event["checkpoint_ref"] is None
    assert event["created_at"]


def test_task_event_store_policy_rejection_prevents_file_creation(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = RejectEventPolicy(store)
    event_store = TaskEventStore(runtime, policy)

    with pytest.raises(PermissionError, match="rejected task_event_write"):
        event_store.write(
            workspace=workspace,
            task_id="task-1",
            event_type="started",
            reason="run started",
        )

    assert policy.calls.count("effect:task_event_write") == 1
    assert not (runtime.state / "events").exists()


def test_policy_allows_task_event_write_only_under_runtime_events(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = PolicyEngine(store)

    allowed = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "task_event_write",
            "path": str(runtime.state / "events" / "task-1" / "started.json"),
        },
    )
    workspace_path = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "task_event_write",
            "path": str(workspace_root / "event.json"),
        },
    )
    source_path = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "task_event_write", "path": str(Path.cwd() / "event.json")},
    )

    assert allowed.allowed is True
    assert workspace_path.allowed is False
    assert source_path.allowed is False


def test_harness_success_records_started_and_completed_events(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="plan only",
    )

    event_paths = [Path(path) for path in result["event_artifacts"]]
    events = [read_event(path) for path in event_paths]
    assert [event["event_type"] for event in events] == ["started", "completed"]
    assert len({event["task_id"] for event in events}) == 1
    assert events[0]["task_id"] == result["task_id"]
    assert events[1]["checkpoint_ref"] == result["approval_artifact"]


def test_harness_failed_inspect_records_failed_event_without_memory(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    with pytest.raises(FileNotFoundError):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request="inspect missing.txt",
        )

    event_files = sorted((runtime.state / "events").glob("*/*.json"))
    events = [read_event(path) for path in event_files]
    assert [event["event_type"] for event in events] == ["started", "failed"]
    assert len({event["task_id"] for event in events}) == 1
    assert "inspect target does not exist" in events[-1]["reason"]
    assert not (runtime.memory / workspace.workspace_id).exists()


def test_harness_failed_directory_inspect_records_failed_event_without_memory(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    directory = workspace_root / "docs"
    directory.mkdir()

    with pytest.raises(BaiUserError, match="inspect target is not a file"):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request="inspect docs",
        )

    event_files = sorted((runtime.state / "events").glob("*/*.json"))
    events = [read_event(path) for path in event_files]
    assert [event["event_type"] for event in events] == ["started", "failed"]
    assert "inspect target is not a file" in events[-1]["reason"]
    assert not (runtime.memory / workspace.workspace_id).exists()


def test_harness_failed_event_write_does_not_mask_original_failure(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    event_store = FailFailedEventStore(runtime, PolicyEngine(store))

    with pytest.raises(FileNotFoundError, match="inspect target does not exist") as exc:
        Harness(runtime=runtime, workspaces=store, events=event_store).run(
            workspace_ref=workspace.workspace_id,
            request="inspect missing.txt",
        )

    notes = getattr(exc.value, "__notes__", [])
    assert any("failed event write failed" in note for note in notes)


def test_harness_internal_bug_is_not_recorded_as_normal_failed_task(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    with pytest.raises(AssertionError, match="agent invariant exploded"):
        Harness(runtime=runtime, workspaces=store, agent=BuggyAgent()).run(
            workspace_ref=workspace.workspace_id,
            request="plan only",
        )

    event_files = sorted((runtime.state / "events").glob("*/*.json"))
    events = [read_event(path) for path in event_files]
    assert [event["event_type"] for event in events] == ["started"]


def test_harness_internal_value_error_is_not_recorded_as_normal_failed_task(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    with pytest.raises(ValueError, match="internal value invariant exploded"):
        Harness(runtime=runtime, workspaces=store, agent=ValueErrorBuggyAgent()).run(
            workspace_ref=workspace.workspace_id,
            request="plan only",
        )

    event_files = sorted((runtime.state / "events").glob("*/*.json"))
    events = [read_event(path) for path in event_files]
    assert [event["event_type"] for event in events] == ["started"]
