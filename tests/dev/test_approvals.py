from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import bai.execution.effects as effects_module
from bai.artifacts.approvals import ApprovalStore
from bai.execution.harness import Harness
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.config.router import ModelRoute
from bai.core.runtime import RuntimePaths
from bai.config.workspace import WorkspaceRecord, WorkspaceStore


class RejectApprovalPolicy(PolicyEngine):
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
        if effect["kind"] == "approval_write":
            return PolicyDecision(False, "rejected approval_write")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


class ChangingMutationApprovalStore(ApprovalStore):
    def write_mutation_approval(self, **kwargs) -> Path:
        path = super().write_mutation_approval(**kwargs)
        Path(kwargs["approved_path"]).write_text("external change\n")
        return path


class MutatingAgentOutputPolicy(PolicyEngine):
    def __init__(self, store: WorkspaceStore, target: Path) -> None:
        super().__init__(store)
        self.target = target

    def gate_agent_output(self, output: dict[str, Any]) -> PolicyDecision:
        decision = super().gate_agent_output(output)
        output.setdefault("proposed_changes", []).append(
            {
                "path": str(self.target),
                "operation": "create",
                "content": "policy mutation\n",
                "rationale": "policy must not mutate executable output",
                "requires_approval": True,
            }
        )
        return decision


@dataclass(frozen=True)
class ModifyPlanAgent:
    target: Path
    content: str = "new content\n"

    def run(
        self,
        *,
        request: str,
        workspace: WorkspaceRecord,
        execution_policy: str,
        model_route: ModelRoute,
    ) -> dict[str, Any]:
        change = {
            "path": str(self.target),
            "operation": "modify",
            "content": self.content,
            "rationale": "bounded phase-one modify proposal",
            "requires_approval": True,
        }
        return {
            "id": "plan-modify",
            "agent_kind": "plan",
            "workspace_id": workspace.workspace_id,
            "execution_policy": execution_policy,
            "model": {
                "provider": model_route.provider,
                "model": model_route.model,
                "endpoint": model_route.endpoint,
            },
            "summary": request,
            "ordered_steps": [],
            "risks": [],
            "requested_approval_scope": [change],
            "proposed_changes": [change],
            "proposed_effects": [],
        }


def read_approval(path: Path) -> dict:
    return json.loads(path.read_text())


def test_approval_store_writes_agent_output_acceptance_shape(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    approval_store = ApprovalStore(runtime, PolicyEngine(store))

    path = approval_store.write_agent_output_acceptance(
        workspace=workspace,
        task_id="task-1",
        agent_output_id="plan-1",
        bound_to_artifacts=[],
        approval_scope={"effects": [], "mutations": []},
    )

    assert path.is_relative_to(runtime.state / "approvals")
    approval = read_approval(path)
    assert approval["approval_id"]
    assert approval["kind"] == "agent_output_acceptance"
    assert approval["workspace_id"] == workspace.workspace_id
    assert approval["task_id"] == "task-1"
    assert approval["agent_output_id"] == "plan-1"
    assert approval["status"] == "accepted"
    assert approval["bound_to_artifacts"] == []
    assert approval["approval_scope"] == {"effects": [], "mutations": []}
    assert "completed" not in approval["status"]
    assert approval["created_at"]


def test_approval_store_policy_rejection_prevents_file_creation(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = RejectApprovalPolicy(store)
    approval_store = ApprovalStore(runtime, policy)

    with pytest.raises(PermissionError, match="rejected approval_write"):
        approval_store.write_agent_output_acceptance(
            workspace=workspace,
            task_id="task-1",
            agent_output_id="plan-1",
            bound_to_artifacts=[],
            approval_scope={},
        )

    assert policy.calls.count("effect:approval_write") == 1
    assert not (runtime.state / "approvals").exists()


def test_successful_run_approval_is_acceptance_not_completion(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request="plan only",
    )

    approval = read_approval(Path(result["approval_artifact"]))
    event_types = [
        json.loads(Path(path).read_text())["event_type"]
        for path in result["event_artifacts"]
    ]
    assert approval["kind"] == "agent_output_acceptance"
    assert approval["status"] == "accepted"
    assert approval["task_id"] == result["task_id"]
    assert "completed" not in approval["status"]
    assert event_types == ["started", "completed"]


def test_failed_inspect_acceptance_does_not_claim_completion(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    with pytest.raises(FileNotFoundError):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request="inspect missing.txt",
        )

    approvals = sorted((runtime.state / "approvals").glob("*.json"))
    assert len(approvals) == 1
    approval = read_approval(approvals[0])
    assert approval["kind"] == "agent_output_acceptance"
    assert approval["status"] == "accepted"
    assert "completed" not in approval["status"]


def test_unapproved_mutation_proposal_does_not_create_mutation_approval(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"propose-create {target}",
    )

    approvals = [read_approval(Path(path)) for path in result["approval_artifacts"]]
    assert [approval["kind"] for approval in approvals] == ["agent_output_acceptance"]
    assert not target.exists()


def test_approved_mutation_creates_mutation_approval_scope(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"propose-create {target}",
        approved_mutation_paths=[str(target)],
    )

    approvals = [read_approval(Path(path)) for path in result["approval_artifacts"]]
    mutation_approvals = [
        approval for approval in approvals if approval["kind"] == "mutation_approval"
    ]
    assert len(mutation_approvals) == 1
    approval = mutation_approvals[0]
    assert approval["workspace_id"] == workspace.workspace_id
    assert approval["task_id"] == result["task_id"]
    assert approval["status"] == "approved"
    assert approval["approval_scope"] == {
        "workspace_id": workspace.workspace_id,
        "approved_path": str(target.resolve()),
        "operation": "create",
        "approving_mechanism": "cli_flag",
        "task_id": result["task_id"],
        "content_sha256": hashlib.sha256(
            b"# created by bai phase-one harness\n"
        ).hexdigest(),
        "expected_preimage": {"exists": False, "sha256": None},
    }


def test_mutation_approval_preimage_change_blocks_harness_write(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"
    approvals = ChangingMutationApprovalStore(runtime, PolicyEngine(store))

    with pytest.raises(PermissionError, match="mutation target changed after approval"):
        Harness(runtime=runtime, workspaces=store, approvals=approvals).run(
            workspace_ref=workspace.workspace_id,
            request=f"propose-create {target}",
            approved_mutation_paths=[str(target)],
        )

    assert target.read_text() == "external change\n"


def test_policy_cannot_mutate_agent_output_after_validation(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "policy-created.md"
    policy = MutatingAgentOutputPolicy(store, target)

    result = Harness(runtime=runtime, workspaces=store, policy=policy).run(
        workspace_ref=workspace.workspace_id,
        request="plan only",
        approved_mutation_paths=[str(target)],
    )

    assert result["applied_changes"] == []
    assert result["denied_changes"] == []
    assert not target.exists()
    approval = read_approval(Path(result["approval_artifact"]))
    assert approval["approval_scope"]["mutations"] == []


def test_create_mutation_fails_when_target_already_exists_before_approval_write(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"
    target.write_text("existing content\n")

    with pytest.raises(PermissionError, match="create target already exists"):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request=f"propose-create {target}",
            approved_mutation_paths=[str(target)],
        )

    assert target.read_text() == "existing content\n"
    approvals = [read_approval(path) for path in sorted((runtime.state / "approvals").glob("*.json"))]
    assert [approval["kind"] for approval in approvals] == ["agent_output_acceptance"]


def test_create_race_after_preimage_check_keeps_external_file(
    runtime: RuntimePaths,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"
    original_link = os.link

    def create_external_file_before_link(src: str | bytes, dst: str | bytes) -> None:
        if Path(dst) == target:
            target.write_text("external race\n")
        original_link(src, dst)

    monkeypatch.setattr(os, "link", create_external_file_before_link)

    with pytest.raises(PermissionError, match="create target already exists"):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request=f"propose-create {target}",
            approved_mutation_paths=[str(target)],
        )

    assert target.read_text() == "external race\n"


def test_modify_mutation_fails_when_target_is_missing_before_approval_write(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "missing.txt"

    with pytest.raises(PermissionError, match="modify target does not exist"):
        Harness(
            runtime=runtime,
            workspaces=store,
            agent=ModifyPlanAgent(target),
        ).run(
            workspace_ref=workspace.workspace_id,
            request="propose modify missing",
            approved_mutation_paths=[str(target)],
        )

    assert not target.exists()
    approvals = [read_approval(path) for path in sorted((runtime.state / "approvals").glob("*.json"))]
    assert [approval["kind"] for approval in approvals] == ["agent_output_acceptance"]


def test_modify_replace_failure_keeps_existing_target_content(
    runtime: RuntimePaths,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "existing.txt"
    target.write_text("old content\n")
    original_replace = os.replace

    def fail_target_replace(src: str | bytes, dst: str | bytes) -> None:
        if Path(dst) == target:
            raise OSError("simulated replace failure")
        original_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_target_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        Harness(
            runtime=runtime,
            workspaces=store,
            agent=ModifyPlanAgent(target),
        ).run(
            workspace_ref=workspace.workspace_id,
            request="propose modify existing",
            approved_mutation_paths=[str(target)],
        )

    assert target.read_text() == "old content\n"
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_mutation_preimage_approval_and_write_are_guarded_by_runtime_lock(
    runtime: RuntimePaths,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if effects_module.fcntl is None:
        pytest.skip("fcntl lock support is unavailable on this platform")
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "created.md"
    calls: list[int] = []

    def record_flock(file_descriptor: int, operation: int) -> None:
        calls.append(operation)

    monkeypatch.setattr(effects_module.fcntl, "flock", record_flock)

    Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"propose-create {target}",
        approved_mutation_paths=[str(target)],
    )

    assert calls[0] == effects_module.fcntl.LOCK_EX
    assert calls[-1] == effects_module.fcntl.LOCK_UN
    lock_files = sorted((runtime.state / "mutation_locks").glob("*.lock"))
    assert len(lock_files) == 1
    assert lock_files[0].is_relative_to(runtime.state / "mutation_locks")


def test_mutation_lock_path_must_stay_under_runtime_home(
    runtime: RuntimePaths,
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    bad_runtime = RuntimePaths(
        home=runtime.home,
        config=runtime.config,
        state=(tmp_path / "outside-state").resolve(),
        logs=runtime.logs,
        memory=runtime.memory,
        cache=runtime.cache,
        sandbox=runtime.sandbox,
    )
    target = workspace_root / "created.md"

    with pytest.raises(ValueError, match="path must stay under runtime home"):
        with Harness(runtime=bad_runtime).effects.mutation_guard(target):
            pass

    assert not (tmp_path / "outside-state").exists()


def test_stale_directory_lock_error_names_lock_and_target(
    runtime: RuntimePaths,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace_root / "created.md"
    lock_dir = runtime.state / "mutation_locks"
    lock_dir.mkdir(parents=True)
    lock_name = hashlib.sha256(str(target.resolve()).encode("utf-8")).hexdigest() + ".lock"
    stale_lock = lock_dir / f"{lock_name}.d"
    stale_lock.mkdir()
    monkeypatch.setattr(effects_module, "fcntl", None)

    with pytest.raises(PermissionError) as exc:
        with Harness(runtime=runtime).effects.mutation_guard(target.resolve()):
            pass

    message = str(exc.value)
    assert str(stale_lock) in message
    assert str(target.resolve()) in message


def test_out_of_scope_approved_mutation_does_not_read_preimage_before_policy(
    runtime: RuntimePaths,
    workspace_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    outside_target = tmp_path / "outside.txt"
    outside_target.write_text("outside\n")
    original_exists = Path.exists
    original_read_bytes = Path.read_bytes

    def fail_if_outside_exists(path: Path) -> bool:
        if path == outside_target:
            raise AssertionError("out-of-scope target was touched before policy gate")
        return original_exists(path)

    def fail_if_outside_read_bytes(path: Path) -> bytes:
        if path == outside_target:
            raise AssertionError("out-of-scope target was read before policy gate")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "exists", fail_if_outside_exists)
    monkeypatch.setattr(Path, "read_bytes", fail_if_outside_read_bytes)

    with pytest.raises(PermissionError, match="outside allowed workspace paths"):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request=f"propose-create {outside_target}",
            approved_mutation_paths=[str(outside_target)],
        )

    assert outside_target.read_text() == "outside\n"


def test_file_preimage_hashes_existing_files_without_read_bytes(
    runtime: RuntimePaths,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "existing.txt"
    target.write_text("old content\n")
    original_read_bytes = Path.read_bytes

    def fail_for_target(path: Path) -> bytes:
        if path == target:
            raise AssertionError("preimage hashing must stream, not read all bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_for_target)

    result = Harness(
        runtime=runtime,
        workspaces=store,
        agent=ModifyPlanAgent(target),
    ).run(
        workspace_ref=workspace.workspace_id,
        request="propose modify existing",
        approved_mutation_paths=[str(target)],
    )

    approvals = [read_approval(Path(path)) for path in result["approval_artifacts"]]
    mutation_approval = [
        approval for approval in approvals if approval["kind"] == "mutation_approval"
    ][0]
    assert mutation_approval["approval_scope"]["expected_preimage"] == {
        "exists": True,
        "sha256": hashlib.sha256(b"old content\n").hexdigest(),
    }
