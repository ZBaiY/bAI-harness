from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.execution.harness import Harness
from bai.artifacts.memory import MemoryStore
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.core.runtime import RuntimePaths
from bai.execution.scheduler import Scheduler
from bai.config.workspace import WorkspaceStore
from conftest import assert_not_under, assert_under


def test_runtime_defaults_to_user_bai_home_when_env_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.delenv("BAI_HOME", raising=False)
    monkeypatch.setenv("HOME", str(fake_home))

    runtime = RuntimePaths.discover()

    assert runtime.home == (fake_home / ".bai").resolve()
    assert runtime.config == runtime.home / "config"
    assert runtime.state == runtime.home / "state"
    assert runtime.memory == runtime.home / "memory"


def test_workspace_config_location_and_canonicalization(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root / "." / "nested" / "..")

    assert Path(record.root_path) == workspace_root.resolve()
    assert record.allowed_paths == [str(workspace_root.resolve())]
    assert record.command_policy["inspect"]["allowed"] is True
    assert record.command_policy["mutate"]["allowed"] is False
    assert record.network_policy == {"workspace_network": False}
    assert record.sandbox_path.startswith(str(runtime.sandbox))

    config_file = runtime.config / "workspaces" / f"{record.workspace_id}.json"
    assert config_file.exists()
    assert config_file.is_relative_to(runtime.home)
    assert not config_file.is_relative_to(workspace_root)

    loaded = store.get("demo")
    assert loaded.workspace_id == record.workspace_id


def test_workspace_name_lookup_uses_index_without_scanning_configs(
    runtime: RuntimePaths,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)

    def fail_scan() -> list:
        raise AssertionError("workspace name lookup must not scan every config")

    monkeypatch.setattr(store, "list", fail_scan)

    loaded = store.get("demo")

    assert loaded.workspace_id == record.workspace_id


def test_workspace_name_lookup_missing_index_has_recovery_guidance(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    store = WorkspaceStore(runtime)
    store.add("demo", workspace_root)
    store._name_index_path("demo").unlink()

    with pytest.raises(KeyError, match="use workspace id or re-add workspace"):
        store.get("demo")


def test_workspace_name_lookup_stale_index_has_recovery_guidance(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    (runtime.config / "workspaces" / f"{record.workspace_id}.json").unlink()

    with pytest.raises(KeyError, match="use workspace id or re-add workspace"):
        store.get("demo")


def test_ambiguous_workspace_name_lookup_uses_index_without_scanning_configs(
    runtime: RuntimePaths,
    workspace_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second_root = tmp_path / "second-workspace"
    second_root.mkdir()
    store = WorkspaceStore(runtime)
    store.add("demo", workspace_root)
    store.add("demo", second_root)

    def fail_scan() -> list:
        raise AssertionError("workspace name ambiguity must not scan every config")

    monkeypatch.setattr(store, "list", fail_scan)

    with pytest.raises(KeyError, match="ambiguous"):
        store.get("demo")


def test_runtime_state_is_under_bai_home(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=record.workspace_id,
        request="draft a bounded plan",
    )

    approval = Path(result["approval_artifact"])
    memory = Path(result["memory_artifact"])
    assert approval.is_relative_to(runtime.state)
    assert memory.is_relative_to(runtime.memory)
    assert approval.exists()
    assert memory.exists()


def test_runtime_artifacts_and_dirs_stay_under_bai_home_not_workspace(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=record.workspace_id,
        request="plan only",
    )

    config_file = runtime.config / "workspaces" / f"{record.workspace_id}.json"
    runtime_dirs = [
        runtime.config,
        runtime.state,
        runtime.logs,
        runtime.memory,
        runtime.cache,
        runtime.sandbox,
    ]
    artifact_paths = [
        config_file,
        Path(result["approval_artifact"]),
        Path(result["memory_artifact"]),
    ]

    for path in runtime_dirs + artifact_paths:
        assert_under(path, runtime.home)
        assert_not_under(path, workspace_root)


def test_harness_defaults_to_serial_execution(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=record.name,
        request="plan only",
    )

    assert result["execution_policy"] == "serial"
    assert result["plan"]["execution_policy"] == "serial"


def test_policy_gate_runs_before_execution(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    class RecordingPolicy(PolicyEngine):
        def __init__(self) -> None:
            super().__init__(WorkspaceStore(runtime))
            self.calls: list[str] = []

        def gate_agent_output(self, output: dict) -> PolicyDecision:
            self.calls.append("agent_output")
            return super().gate_agent_output(output)

        def gate_effect(self, *, workspace, effect, approved=False) -> PolicyDecision:
            self.calls.append(f"effect:{effect['kind']}")
            return super().gate_effect(
                workspace=workspace, effect=effect, approved=approved
            )

    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    policy = RecordingPolicy()

    Harness(runtime=runtime, workspaces=store, policy=policy).run(
        workspace_ref=record.workspace_id,
        request="plan only",
    )

    assert policy.calls.index("agent_output") < policy.calls.index(
        "effect:approval_write"
    )
    assert "effect:approval_write" in policy.calls
    assert "effect:memory_write" in policy.calls
    assert policy.calls.index("effect:approval_write") < policy.calls.index(
        "effect:memory_write"
    )
    assert policy.calls.count("effect:task_event_write") == 2


def test_no_mutation_without_approval(runtime: RuntimePaths, workspace_root: Path) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    target = workspace_root / "created.md"

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=record.workspace_id,
        request=f"propose-create {target}",
    )

    assert result["plan"]["proposed_changes"]
    assert result["applied_changes"] == []
    assert result["status"] == "completed_with_denials"
    assert result["denied_changes"] == [
        {
            "path": str(target.resolve()),
            "operation": "create",
            "status": "denied",
            "reason": "mutation requires harness approval",
        }
    ]
    assert not target.exists()


def test_one_scoped_mutation_after_approval(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    target = workspace_root / "created.md"

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=record.workspace_id,
        request=f"propose-create {target}",
        approved_mutation_paths=[str(target)],
    )

    assert result["applied_changes"] == [
        {"path": str(target.resolve()), "operation": "create"}
    ]
    assert target.read_text() == "# created by bai phase-one harness\n"


def test_minimal_memory_write_boundaries(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    memory = MemoryStore(runtime, PolicyEngine(store))

    session_path = memory.write(
        workspace=record,
        kind="session",
        content={"message": "hello"},
    )
    working_path = memory.write(
        workspace=record,
        kind="working",
        content={"note": "scratch"},
    )

    assert session_path.is_relative_to(runtime.memory / record.workspace_id / "session")
    assert working_path.is_relative_to(runtime.memory / record.workspace_id / "working")
    assert json.loads(session_path.read_text())["kind"] == "session"

    with pytest.raises(ValueError):
        memory.write(workspace=record, kind="long_term", content={})


def test_toy_scheduler_pause_transition() -> None:
    scheduler = Scheduler()
    task = scheduler.start_toy_task("toy-1", "background indexing placeholder")

    assert task.status == "running"
    scheduler.pause(task)
    assert task.status == "paused"
