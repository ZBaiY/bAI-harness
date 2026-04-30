from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from bai.execution.agent import PlanAgent
from bai.core.errors import BaiUserError
from bai.execution.harness import Harness
from bai.core.policy import PolicyDecision, PolicyEngine
from bai.config.router import ModelRoute
from bai.core.runtime import RuntimePaths
from bai.config.workspace import WorkspaceRecord, WorkspaceStore


class RecordingRejectTestPolicy(PolicyEngine):
    def __init__(self, store: WorkspaceStore) -> None:
        super().__init__(store)
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
        if effect["kind"] == "test_command":
            return PolicyDecision(False, "rejected test_command")
        return super().gate_effect(workspace=workspace, effect=effect, approved=approved)


class FailingIfCalledExecutor:
    def run(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("test command executed before policy gate allowed it")


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def assert_calls_in_order(calls: list[str], expected: list[str]) -> None:
    cursor = 0
    for call in expected:
        assert call in calls[cursor:]
        cursor = calls.index(call, cursor) + 1


def test_plan_agent_emits_test_command_only_for_explicit_test(
    workspace_root: Path,
) -> None:
    workspace = WorkspaceRecord(
        workspace_id="demo-id",
        name="demo",
        root_path=str(workspace_root),
        allowed_paths=[str(workspace_root)],
        default_branch=None,
        command_policy={"inspect": {"allowed": True}, "test": {"allowed": False}},
        network_policy={"workspace_network": False},
        sandbox_path=str(workspace_root / ".sandbox"),
        memory_scope="workspace:demo-id",
    )
    route = ModelRoute(provider="local", model="local-plan-stub", endpoint="stub://local/plan")
    argv = [sys.executable, "-c", "print('ok')"]

    test_output = PlanAgent().run(
        request=f"test {shlex.join(argv)}",
        workspace=workspace,
        execution_policy="serial",
        model_route=route,
    )
    plan_output = PlanAgent().run(
        request="plan only",
        workspace=workspace,
        execution_policy="serial",
        model_route=route,
    )

    assert test_output["proposed_effects"] == [
        {"kind": "test_command", "argv": argv, "declared_writable_paths": []}
    ]
    assert plan_output["proposed_effects"] == []


def test_agent_output_policy_rejects_empty_test_command(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    output = PlanAgent().run(
        request="test",
        workspace=workspace,
        execution_policy="serial",
        model_route=ModelRoute(
            provider="local",
            model="local-plan-stub",
            endpoint="stub://local/plan",
        ),
    )
    decision = PolicyEngine(store).gate_agent_output(output)

    assert decision.allowed is False
    assert "test command requires argv" in decision.reason


def test_policy_denies_test_command_by_default(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    decision = PolicyEngine(store).gate_effect(
        workspace=workspace,
        effect={
            "kind": "test_command",
            "argv": [sys.executable, "-c", "print('ok')"],
            "cwd": str(workspace_root),
            "declared_writable_paths": [],
        },
    )

    assert decision.allowed is False
    assert "test command policy denies execution" in decision.reason


def test_workspace_allow_test_policy_allows_exact_argv_only(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [sys.executable, "-c", "print('ok')"]
    workspace = store.allow_test(
        workspace.workspace_id,
        argv=argv,
        writable_paths=[workspace_root / ".pytest_cache"],
    )
    policy = PolicyEngine(store)

    allowed = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "test_command",
            "argv": argv,
            "cwd": str(workspace_root),
            "declared_writable_paths": [str((workspace_root / ".pytest_cache").resolve())],
        },
    )
    disallowed = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "test_command",
            "argv": [sys.executable, "-c", "print('no')"],
            "cwd": str(workspace_root),
            "declared_writable_paths": [],
        },
    )

    assert allowed.allowed is True
    assert disallowed.allowed is False
    assert "argv not approved" in disallowed.reason
    assert workspace.command_policy["test"]["write_enforcement"] == "trusted_command_no_sandbox"


def test_workspace_allow_test_policy_allows_runtime_cache_writable_path(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [sys.executable, "-c", "print('ok')"]
    cache_path = runtime.cache / "pytest" / workspace.workspace_id

    workspace = store.allow_test(
        workspace.workspace_id,
        argv=argv,
        writable_paths=[cache_path],
    )
    decision = PolicyEngine(store).gate_effect(
        workspace=workspace,
        effect={
            "kind": "test_command",
            "argv": argv,
            "cwd": str(workspace_root),
            "declared_writable_paths": [str(cache_path)],
        },
    )

    assert decision.allowed is True


def test_workspace_allow_test_policy_supports_argv_prefix(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    workspace = store.allow_test(
        workspace.workspace_id,
        argv_prefix=[sys.executable, "-m", "pytest"],
        writable_paths=[],
    )

    decision = PolicyEngine(store).gate_effect(
        workspace=workspace,
        effect={
            "kind": "test_command",
            "argv": [sys.executable, "-m", "pytest", "-q"],
            "cwd": str(workspace_root),
            "declared_writable_paths": [],
        },
    )

    assert decision.allowed is True


def test_policy_rejects_shell_string_wrong_cwd_and_unapproved_writable_path(
    runtime: RuntimePaths, workspace_root: Path, tmp_path: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [sys.executable, "-c", "print('ok')"]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])
    policy = PolicyEngine(store)

    shell_string = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "test_command", "argv": "python -m pytest", "cwd": str(workspace_root)},
    )
    wrong_cwd = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "test_command",
            "argv": argv,
            "cwd": str(tmp_path),
            "declared_writable_paths": [],
        },
    )
    bad_writable = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "test_command",
            "argv": argv,
            "cwd": str(workspace_root),
            "declared_writable_paths": [str(tmp_path / "outside-cache")],
        },
    )

    assert shell_string.allowed is False
    assert "argv must be a JSON array" in shell_string.reason
    assert wrong_cwd.allowed is False
    assert "cwd must be the workspace root" in wrong_cwd.reason
    assert bad_writable.allowed is False
    assert "writable path is not approved" in bad_writable.reason


def test_task_scope_cannot_broaden_test_command_policy(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    decision = PolicyEngine(store).gate_effect(
        workspace=workspace,
        effect={
            "kind": "test_command",
            "argv": [sys.executable, "-c", "print('ok')"],
            "cwd": str(workspace_root),
            "declared_writable_paths": [],
            "task_scope": {"command_policy": {"test": {"allowed": True}}},
        },
    )

    assert decision.allowed is False
    assert "test command policy denies execution" in decision.reason
    for command_class in ["mutate", "workspace_network", "destructive"]:
        assert workspace.command_policy[command_class]["allowed"] is False


def test_policy_gate_blocks_test_command_before_subprocess_execution(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [sys.executable, "-c", "print('ok')"]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])
    policy = RecordingRejectTestPolicy(store)

    with pytest.raises(PermissionError, match="rejected test_command"):
        Harness(
            runtime=runtime,
            workspaces=store,
            policy=policy,
            test_executor=FailingIfCalledExecutor(),
        ).run(
            workspace_ref=workspace.workspace_id,
            request=f"test {shlex.join(argv)}",
        )

    assert_calls_in_order(
        policy.calls,
        [
            "agent_output",
            "effect:approval_write",
            "effect:test_command",
            "effect:task_event_write",
        ],
    )
    assert not (runtime.memory / workspace.workspace_id).exists()


def test_test_command_nonzero_exit_fails_run_without_memory(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [sys.executable, "-c", "import sys; sys.exit(7)"]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])

    with pytest.raises(BaiUserError, match="test command failed with exit code 7"):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request=f"test {shlex.join(argv)}",
        )

    events = [
        read_json(path)["event_type"]
        for path in sorted((runtime.state / "events").glob("*/*.json"))
    ]
    assert events == ["started", "failed"]
    assert not (runtime.memory / workspace.workspace_id).exists()


def test_test_command_timeout_fails_run_without_retry(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [sys.executable, "-c", "import time; time.sleep(2)"]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])

    with pytest.raises(BaiUserError, match="test command timed out"):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=workspace.workspace_id,
            request=f"test {shlex.join(argv)}",
        )

    events = [
        read_json(path)["event_type"]
        for path in sorted((runtime.state / "events").glob("*/*.json"))
    ]
    assert events == ["started", "failed"]
    assert not (runtime.memory / workspace.workspace_id).exists()


def test_test_command_writable_symlink_escape_is_rejected_when_supported(
    runtime: RuntimePaths, workspace_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace_root / "cache_link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    with pytest.raises(BaiUserError, match="writable path outside workspace"):
        store.allow_test(
            workspace.workspace_id,
            argv=[sys.executable, "-c", "print('ok')"],
            writable_paths=[link],
        )


def test_test_command_stdout_and_stderr_are_capped(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    argv = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.write('x'*5000); sys.stderr.write('e'*5000)",
    ]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"test {shlex.join(argv)}",
    )

    test_run = result["test_runs"][0]
    assert len(test_run["stdout_preview"]) == 4096
    assert len(test_run["stderr_preview"]) == 4096
    assert test_run["stdout_truncated"] is True
    assert test_run["stderr_truncated"] is True


def test_test_command_result_declares_trusted_no_sandbox_write_model(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    target = workspace_root / "source.txt"
    target.write_text("before\n")
    argv = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('source.txt').write_text('after\\n')",
    ]
    workspace = store.allow_test(workspace.workspace_id, argv=argv, writable_paths=[])

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=workspace.workspace_id,
        request=f"test {shlex.join(argv)}",
    )

    test_run = result["test_runs"][0]
    assert target.read_text() == "after\n"
    assert test_run["write_enforcement"] == "trusted_command_no_sandbox"
    assert test_run["declared_writable_paths"] == []
