from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from bai.core.runtime import RuntimePaths
from conftest import assert_not_under, assert_under, load_json_output


REPO_ROOT = Path(__file__).resolve().parents[2]
DECLARED_CLI_TARGET = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())[
    "project"
]["scripts"]["bai"]
DEV_WORKFLOW_EDGES = [
    {"from": "plan", "to": "code"},
    {"from": "code", "to": "doc"},
    {"from": "code", "to": "test"},
    {"from": "doc", "to": "audit"},
    {"from": "test", "to": "audit"},
    {"from": "audit", "to": "fix"},
]
DEV_WORKFLOW_DEPENDS_ON = [
    [],
    ["plan"],
    ["code"],
    ["code"],
    ["doc", "test"],
    ["audit"],
]


def run_declared_bai(
    *,
    argv: list[str],
    bai_home: Path,
    cwd: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    script = (
        "import importlib, sys; "
        "module_name, func_name = sys.argv[1].split(':'); "
        "func = getattr(importlib.import_module(module_name), func_name); "
        "raise SystemExit(func(sys.argv[2:]))"
    )
    env = os.environ.copy()
    env["BAI_HOME"] = str(bai_home)
    src_path = str((cwd / "src").resolve())
    env["PYTHONPATH"] = (
        src_path
        if not env.get("PYTHONPATH")
        else os.pathsep.join([src_path, env["PYTHONPATH"]])
    )
    return subprocess.run(
        [sys.executable, "-c", script, DECLARED_CLI_TARGET, *argv],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def add_workspace(
    bai_home: Path,
    workspace_root: Path,
    *,
    name: str = "demo",
    cwd: Path = REPO_ROOT,
) -> dict[str, Any]:
    result = run_declared_bai(
        argv=["workspace", "add", name, str(workspace_root)],
        bai_home=bai_home,
        cwd=cwd,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    return load_json_output(result.stdout)


def allow_test(
    bai_home: Path,
    workspace_id: str,
    argv: list[str],
    *,
    writable_path: Path | None = None,
    cwd: Path = REPO_ROOT,
) -> dict[str, Any]:
    command = ["workspace", "allow-test", workspace_id, "--argv", json.dumps(argv)]
    if writable_path is not None:
        command.extend(["--writable-path", str(writable_path)])
    result = run_declared_bai(argv=command, bai_home=bai_home, cwd=cwd)
    assert result.returncode == 0
    assert result.stderr == ""
    return load_json_output(result.stdout)


def assert_runtime_artifact(
    path: str | Path,
    runtime: RuntimePaths,
    workspace_root: Path,
    source_root: Path,
) -> None:
    assert Path(path).exists(), path
    assert_under(path, runtime.home)
    assert_not_under(path, workspace_root)
    assert_not_under(path, source_root)


def result_artifact_paths(result: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in [
        "workflow_artifact",
        "context_artifact",
        "scheduler_artifact",
        "approval_artifact",
        "memory_artifact",
        "working_memory_artifact",
        "audit_artifact",
        "fix_artifact",
        "optic_trace_artifact",
    ]:
        if result.get(key):
            paths.append(result[key])
    for key in ["approval_artifacts", "event_artifacts", "memory_artifacts"]:
        paths.extend(result.get(key, []))
    return list(dict.fromkeys(paths))


def snapshot_workspace_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def scheduler_paths(runtime: RuntimePaths) -> set[Path]:
    return set((runtime.state / "scheduler").glob("*.json"))


def memory_artifact_paths(runtime: RuntimePaths, workspace_id: str) -> list[Path]:
    return sorted((runtime.memory / workspace_id).glob("*/*.json"))


def event_task_ids(runtime: RuntimePaths) -> set[str]:
    events_dir = runtime.state / "events"
    if not events_dir.exists():
        return set()
    return {path.name for path in events_dir.iterdir() if path.is_dir()}


def task_event_groups(runtime: RuntimePaths) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((runtime.state / "events").glob("*/*.json")):
        event = read_json(path)
        groups.setdefault(event["task_id"], []).append(event)
    return groups


def failed_task_ids(runtime: RuntimePaths) -> list[str]:
    failed: list[str] = []
    for task_id, events in task_event_groups(runtime).items():
        if [event["event_type"] for event in events] == ["started", "failed"]:
            failed.append(task_id)
    return failed


def assert_no_success_memory_for_task(runtime: RuntimePaths, task_id: str) -> None:
    for path in runtime.memory.glob("*/*/*.json"):
        memory = read_json(path)
        content = memory.get("content", {})
        assert content.get("task_id") != task_id
        assert not str(content.get("workflow_artifact", "")).endswith(f"{task_id}.json")


def assert_success_artifact_identity(
    result: dict[str, Any],
    *,
    workspace_id: str,
) -> None:
    task_id = result["task_id"]
    assert result["workspace_id"] == workspace_id
    assert result["plan"]["workspace_id"] == workspace_id
    assert result["plan"]["execution_policy"] == result["execution_policy"]

    workflow = read_json(result["workflow_artifact"])
    assert workflow["task_id"] == task_id
    assert workflow["workspace_id"] == workspace_id
    assert workflow["execution_policy"] == result["execution_policy"]
    assert workflow["artifacts"]["context_artifact"] == result["context_artifact"]
    assert workflow["artifacts"]["approval_artifacts"] == result["approval_artifacts"]
    assert workflow["artifacts"]["event_artifacts"] == result["event_artifacts"]
    assert workflow["artifacts"]["memory_artifacts"] == result["memory_artifacts"]
    if result.get("audit_artifact"):
        assert workflow["artifacts"]["audit_artifact"] == result["audit_artifact"]
    if result.get("fix_artifact"):
        assert workflow["artifacts"]["fix_artifact"] == result["fix_artifact"]

    context = read_json(result["context_artifact"])
    assert context["task_id"] == task_id
    assert context["workspace_id"] == workspace_id
    assert context["workspace"]["workspace_id"] == workspace_id

    approval_paths = list(
        dict.fromkeys([result["approval_artifact"], *result["approval_artifacts"]])
    )
    for path in approval_paths:
        approval = read_json(path)
        assert approval["task_id"] == task_id
        assert approval["workspace_id"] == workspace_id
        if approval["kind"] == "mutation_approval":
            scope = approval["approval_scope"]
            assert scope["task_id"] == task_id
            assert scope["workspace_id"] == workspace_id

    events = [read_json(path) for path in result["event_artifacts"]]
    assert [event["event_type"] for event in events] == ["started", "completed"]
    for event in events:
        assert event["task_id"] == task_id
        assert event["workspace_id"] == workspace_id

    assert result["memory_artifacts"] == [
        result["memory_artifact"],
        result["working_memory_artifact"],
    ]
    session_memory = read_json(result["memory_artifact"])
    working_memory = read_json(result["working_memory_artifact"])
    assert session_memory["workspace_id"] == workspace_id
    assert session_memory["kind"] == "session"
    assert session_memory["content"]["plan_id"] == result["plan"]["id"]
    assert working_memory["workspace_id"] == workspace_id
    assert working_memory["kind"] == "working"
    assert working_memory["content"]["task_id"] == task_id
    assert working_memory["content"]["workspace_id"] == workspace_id
    assert working_memory["content"]["plan_id"] == result["plan"]["id"]
    assert working_memory["content"]["workflow_artifact"] == result["workflow_artifact"]
    assert working_memory["content"]["approval_artifacts"] == result["approval_artifacts"]
    assert working_memory["content"]["audit_artifact"] == result.get("audit_artifact")
    assert working_memory["content"]["fix_artifact"] == result.get("fix_artifact")

    if result.get("audit_artifact"):
        audit = read_json(result["audit_artifact"])
        assert audit["task_id"] == task_id
        assert audit["workspace_id"] == workspace_id
        assert audit["source_artifacts"]["context_artifact"] == result["context_artifact"]
        assert audit["source_artifacts"]["approval_artifacts"] == result["approval_artifacts"]
        assert audit["source_artifacts"]["event_artifacts"] == result["event_artifacts"]
    if result.get("fix_artifact"):
        fix = read_json(result["fix_artifact"])
        assert fix["task_id"] == task_id
        assert fix["workspace_id"] == workspace_id
        assert fix["source_audit_artifact"] == result["audit_artifact"]

    scheduler = read_json(result["scheduler_artifact"])
    assert scheduler["workspace_id"] == workspace_id
    assert scheduler["harness_task_id"] == task_id
    assert scheduler["run_status"] == result["status"]


def assert_failed_cli_artifacts(
    runtime: RuntimePaths,
    *,
    workspace_id: str,
    scheduler_before: set[Path],
    task_ids_before: set[str],
    memory_before: list[Path],
    expected_error: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    new_scheduler_paths = scheduler_paths(runtime) - scheduler_before
    assert len(new_scheduler_paths) == 1
    scheduler = read_json(new_scheduler_paths.pop())
    assert scheduler["workspace_id"] == workspace_id
    assert scheduler["priority"] == "foreground"
    assert scheduler["lifecycle_status"] == "failed"
    assert scheduler["execution_state"] == "foreground_run_failed"
    assert expected_error in scheduler["failure_reason"]
    assert "harness_task_id" not in scheduler

    new_failed = [
        task_id
        for task_id in failed_task_ids(runtime)
        if task_id not in task_ids_before
    ]
    assert len(new_failed) == 1
    task_id = new_failed[0]
    workflow = read_json(runtime.state / "workflows" / f"{task_id}.json")
    assert workflow["task_id"] == task_id
    assert workflow["workspace_id"] == workspace_id
    assert workflow["status"] == "failed"
    assert workflow["artifacts"]["memory_artifacts"] == []
    assert memory_artifact_paths(runtime, workspace_id) == memory_before

    events = [read_json(path) for path in workflow["artifacts"]["event_artifacts"]]
    assert [event["event_type"] for event in events] == ["started", "failed"]
    for event in events:
        assert event["task_id"] == task_id
        assert event["workspace_id"] == workspace_id

    assert_no_success_memory_for_task(runtime, task_id)
    return task_id, scheduler, workflow


def test_phase_one_global_cli_requires_explicit_workspace(
    runtime: RuntimePaths,
) -> None:
    result = run_declared_bai(argv=["plan only"], bai_home=runtime.home)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "--workspace <name-or-id>" in result.stderr
    assert not runtime.home.exists()

    result = run_declared_bai(
        argv=["--workspace", "missing", "plan only"],
        bai_home=runtime.home,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "workspace name index missing" in result.stderr
    assert not (runtime.state / "events").exists()
    assert not list(runtime.memory.glob("*/*/*.json"))


def test_phase_one_workspace_lifecycle_persists_only_under_bai_home(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    source_root = REPO_ROOT.resolve()
    other_home = runtime.home.parent / ".bai-other"
    workspace = add_workspace(runtime.home, workspace_root)

    listed = run_declared_bai(argv=["workspace", "list"], bai_home=runtime.home)
    assert listed.returncode == 0
    assert listed.stderr == ""
    assert [item["workspace_id"] for item in load_json_output(listed.stdout)] == [
        workspace["workspace_id"]
    ]

    other_list = run_declared_bai(argv=["workspace", "list"], bai_home=other_home)
    assert other_list.returncode == 0
    assert other_list.stderr == ""
    assert load_json_output(other_list.stdout) == []

    other_show = run_declared_bai(
        argv=["workspace", "show", workspace["workspace_id"]],
        bai_home=other_home,
    )
    assert other_show.returncode == 2
    assert other_show.stdout == ""
    assert "workspace name index missing" in other_show.stderr

    listed_again = run_declared_bai(argv=["workspace", "list"], bai_home=runtime.home)
    assert listed_again.returncode == 0
    assert [
        item["workspace_id"] for item in load_json_output(listed_again.stdout)
    ] == [workspace["workspace_id"]]

    shown = run_declared_bai(
        argv=["workspace", "show", workspace["workspace_id"]],
        bai_home=runtime.home,
    )
    assert shown.returncode == 0
    assert shown.stderr == ""
    assert load_json_output(shown.stdout) == workspace

    run = run_declared_bai(
        argv=["run", "--workspace", workspace["workspace_id"], "plan only"],
        bai_home=runtime.home,
    )
    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    assert_success_artifact_identity(result, workspace_id=workspace["workspace_id"])

    config_path = runtime.config / "workspaces" / f"{workspace['workspace_id']}.json"
    name_index = next((runtime.config / "workspace_names").glob("*.json"))
    for path in [config_path, name_index, *result_artifact_paths(result)]:
        assert_runtime_artifact(path, runtime, workspace_root, source_root)
    assert_under(workspace["sandbox_path"], runtime.sandbox)
    assert_not_under(workspace["sandbox_path"], workspace_root)
    assert_not_under(workspace["sandbox_path"], source_root)
    assert not (workspace_root / ".bai").exists()
    assert not (source_root / ".bai").exists()


def test_phase_one_plan_only_run_writes_expected_runtime_artifacts(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    source_root = REPO_ROOT.resolve()
    workspace = add_workspace(runtime.home, workspace_root)
    before = snapshot_workspace_files(workspace_root)

    run = run_declared_bai(
        argv=["run", "--workspace", workspace["workspace_id"], "plan only"],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    assert result["status"] == "success"
    assert result["execution_policy"] == "serial"
    assert result["workspace_id"] == workspace["workspace_id"]
    assert result["model"] == {
        "provider": "local",
        "model": "local-plan-stub",
        "endpoint": "stub://local/plan",
        "network_required": False,
    }
    assert result["audit_artifact"] is None
    assert result["fix_artifact"] is None
    assert result["inspections"] == []
    assert result["applied_changes"] == []
    assert result["denied_changes"] == []
    assert snapshot_workspace_files(workspace_root) == before

    for path in result_artifact_paths(result):
        assert_runtime_artifact(path, runtime, workspace_root, source_root)
    assert_success_artifact_identity(result, workspace_id=workspace["workspace_id"])

    workflow = read_json(result["workflow_artifact"])
    context = read_json(result["context_artifact"])
    approval = read_json(result["approval_artifact"])
    scheduler = read_json(result["scheduler_artifact"])
    event_types = [read_json(path)["event_type"] for path in result["event_artifacts"]]
    session_memory = read_json(result["memory_artifact"])
    working_memory = read_json(result["working_memory_artifact"])

    assert [node["id"] for node in workflow["nodes"]] == ["plan", "memory_write"]
    assert workflow["status"] == "completed"
    assert workflow["artifacts"]["memory_artifacts"] == result["memory_artifacts"]
    assert context["user_request"] == "plan only"
    assert context["workspace"]["allowed_paths"] == [str(workspace_root.resolve())]
    assert "content_preview" not in json.dumps(context)
    assert approval["kind"] == "agent_output_acceptance"
    assert approval["status"] == "accepted"
    assert event_types == ["started", "completed"]
    assert session_memory["kind"] == "session"
    assert session_memory["content"]["request"] == "plan only"
    assert working_memory["kind"] == "working"
    assert working_memory["content"]["task_id"] == result["task_id"]
    assert scheduler["priority"] == "foreground"
    assert scheduler["lifecycle_status"] == "completed"
    assert scheduler["execution_state"] == "foreground_run_completed"
    assert scheduler["harness_task_id"] == result["task_id"]


def test_phase_one_inspect_is_read_only_and_path_scoped(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    workspace = add_workspace(runtime.home, workspace_root)
    target = workspace_root / "README.md"
    target.write_text("alpha\nbeta\n")
    outside = workspace_root.parent / "outside.txt"
    outside.write_text("outside\n")
    before = snapshot_workspace_files(workspace_root)

    run = run_declared_bai(
        argv=["run", "--workspace", workspace["workspace_id"], "inspect README.md"],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    assert len(result["inspections"]) == 1
    inspection = result["inspections"][0]
    assert inspection["path"] == str(target.resolve())
    assert inspection["status"] == "success"
    assert inspection["content_preview"] == "alpha\nbeta\n"
    assert inspection["truncated"] is False
    assert_success_artifact_identity(result, workspace_id=workspace["workspace_id"])
    workflow = read_json(result["workflow_artifact"])
    assert [node["kind"] for node in workflow["nodes"]] == [
        "plan",
        "inspect",
        "memory_write",
    ]
    assert workflow["nodes"][1]["can_mutate"] is False
    assert snapshot_workspace_files(workspace_root) == before

    scheduler_before = scheduler_paths(runtime)
    task_ids_before = event_task_ids(runtime)
    memory_before = memory_artifact_paths(runtime, workspace["workspace_id"])
    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"inspect {outside}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 2
    assert run.stdout == ""
    assert "outside allowed workspace paths" in run.stderr
    assert outside.read_text() == "outside\n"
    assert snapshot_workspace_files(workspace_root) == before
    assert_failed_cli_artifacts(
        runtime,
        workspace_id=workspace["workspace_id"],
        scheduler_before=scheduler_before,
        task_ids_before=task_ids_before,
        memory_before=memory_before,
        expected_error="outside allowed workspace paths",
    )


def test_phase_one_mutation_requires_explicit_approval_and_scope(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    workspace = add_workspace(runtime.home, workspace_root)
    inside_target = workspace_root / "created.md"
    outside_target = workspace_root.parent / "outside-created.md"

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"propose-create {inside_target}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    denied_result = load_json_output(run.stdout)
    assert denied_result["status"] == "completed_with_denials"
    assert denied_result["applied_changes"] == []
    assert len(denied_result["denied_changes"]) == 1
    denied_change = denied_result["denied_changes"][0]
    assert denied_change["path"] == str(inside_target.resolve())
    assert denied_change["operation"] == "create"
    assert denied_change["status"] == "denied"
    assert denied_change["reason"] == "mutation requires harness approval"
    assert not inside_target.exists()
    assert [
        read_json(path)["kind"] for path in denied_result["approval_artifacts"]
    ] == ["agent_output_acceptance"]
    assert_success_artifact_identity(
        denied_result,
        workspace_id=workspace["workspace_id"],
    )

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(inside_target),
            f"propose-create {inside_target}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    approved_result = load_json_output(run.stdout)
    approvals = [read_json(path) for path in approved_result["approval_artifacts"]]
    mutation_approvals = [
        approval for approval in approvals if approval["kind"] == "mutation_approval"
    ]
    assert approved_result["applied_changes"] == [
        {"path": str(inside_target.resolve()), "operation": "create"}
    ]
    assert inside_target.read_text() == "# created by bai phase-one harness\n"
    assert len(mutation_approvals) == 1
    scope = mutation_approvals[0]["approval_scope"]
    assert scope["workspace_id"] == workspace["workspace_id"]
    assert scope["approved_path"] == str(inside_target.resolve())
    assert scope["operation"] == "create"
    assert scope["approving_mechanism"] == "cli_flag"
    assert scope["task_id"] == approved_result["task_id"]
    assert scope["content_sha256"] == hashlib.sha256(
        b"# created by bai phase-one harness\n"
    ).hexdigest()
    assert scope["expected_preimage"] == {"exists": False, "sha256": None}
    assert_success_artifact_identity(
        approved_result,
        workspace_id=workspace["workspace_id"],
    )

    scheduler_before = scheduler_paths(runtime)
    task_ids_before = event_task_ids(runtime)
    memory_before = memory_artifact_paths(runtime, workspace["workspace_id"])
    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(outside_target),
            f"propose-create {outside_target}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 2
    assert run.stdout == ""
    assert "outside allowed workspace paths" in run.stderr
    assert not outside_target.exists()
    assert [
        read_json(path)["kind"]
        for path in sorted((runtime.state / "approvals").glob("*.json"))
    ].count("mutation_approval") == 1
    assert_failed_cli_artifacts(
        runtime,
        workspace_id=workspace["workspace_id"],
        scheduler_before=scheduler_before,
        task_ids_before=task_ids_before,
        memory_before=memory_before,
        expected_error="outside allowed workspace paths",
    )


def test_phase_one_test_command_requires_workspace_policy(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    workspace = add_workspace(runtime.home, workspace_root)
    marker = workspace_root / "test-ran.txt"
    argv = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "Path('test-ran.txt').write_text('ran\\n'); "
            "print('allowed test')"
        ),
    ]
    request = f"test {shlex.join(argv)}"

    scheduler_before = scheduler_paths(runtime)
    task_ids_before = event_task_ids(runtime)
    memory_before = memory_artifact_paths(runtime, workspace["workspace_id"])
    run = run_declared_bai(
        argv=["run", "--workspace", workspace["workspace_id"], request],
        bai_home=runtime.home,
    )

    assert run.returncode == 2
    assert run.stdout == ""
    assert "test command policy denies execution" in run.stderr
    assert not marker.exists()
    assert_failed_cli_artifacts(
        runtime,
        workspace_id=workspace["workspace_id"],
        scheduler_before=scheduler_before,
        task_ids_before=task_ids_before,
        memory_before=memory_before,
        expected_error="test command policy denies execution",
    )

    updated = allow_test(
        runtime.home,
        workspace["workspace_id"],
        argv,
        writable_path=workspace_root / ".pytest_cache",
    )
    assert updated["command_policy"]["test"]["allowed_argv"] == [argv]

    run = run_declared_bai(
        argv=["run", "--workspace", workspace["workspace_id"], request],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    assert marker.read_text() == "ran\n"
    assert result["test_runs"] == [
        {
            "argv": argv,
            "cwd": str(workspace_root.resolve()),
            "exit_code": 0,
            "stdout_preview": "allowed test\n",
            "stderr_preview": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "declared_writable_paths": [],
            "write_enforcement": "trusted_command_no_sandbox",
        }
    ]
    assert_success_artifact_identity(result, workspace_id=workspace["workspace_id"])
    workflow = read_json(result["workflow_artifact"])
    assert workflow["artifacts"]["test_runs"] == result["test_runs"]
    assert workflow["nodes"][1]["effect"]["argv"] == argv
    assert workflow["nodes"][1]["effect"]["write_enforcement"] == (
        "trusted_command_no_sandbox"
    )


def test_phase_one_dev_workflow_records_serial_contract_without_autonomy(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    source_root = REPO_ROOT.resolve()
    workspace = add_workspace(runtime.home, workspace_root)
    before = snapshot_workspace_files(workspace_root)

    run = run_declared_bai(
        argv=["run", "--workspace", workspace["workspace_id"], "dev plan only"],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    workflow = read_json(result["workflow_artifact"])
    audit = read_json(result["audit_artifact"])
    assert result["workflow_mode"] == "dev"
    assert result["execution_policy"] == "serial"
    assert result["applied_changes"] == []
    assert result["denied_changes"] == []
    assert [node["id"] for node in workflow["nodes"]] == [
        "plan",
        "code",
        "doc",
        "test",
        "audit",
        "fix",
    ]
    assert all(
        node["kind"] not in {"parallel_worker", "agent_to_agent"}
        for node in workflow["nodes"]
    )
    assert workflow["nodes"][1]["approval_required"] is True
    assert workflow["nodes"][1]["can_mutate"] is True
    assert workflow["nodes"][2]["can_mutate"] is False
    assert workflow["nodes"][4]["can_mutate"] is False
    assert workflow["nodes"][5]["auto_apply"] is False
    assert result["node_results"]["code"]["status"] == "skipped"
    assert result["node_results"]["doc"]["status"] == "proposal_only"
    assert result["node_results"]["test"]["status"] == "skipped"
    assert result["node_results"]["audit"]["status"] == "passed_with_notes"
    assert result["node_results"]["fix"]["status"] == "skipped"
    assert result["fix_artifact"] is None
    assert result["fix_proposals"] == []
    assert audit["findings"] == result["audit_findings"]
    assert audit["source_artifacts"]["workflow_artifact"] == result["workflow_artifact"]
    assert_success_artifact_identity(result, workspace_id=workspace["workspace_id"])
    for path in result_artifact_paths(result):
        assert_runtime_artifact(path, runtime, workspace_root, source_root)
    assert snapshot_workspace_files(workspace_root) == before

    denied_target = workspace_root / "dev-denied.md"
    before_denied = snapshot_workspace_files(workspace_root)
    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"dev propose-create {denied_target}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    denied_result = load_json_output(run.stdout)
    assert denied_result["status"] == "completed_with_denials"
    assert denied_result["applied_changes"] == []
    assert len(denied_result["denied_changes"]) == 1
    assert denied_result["denied_changes"][0]["path"] == str(denied_target.resolve())
    assert denied_result["denied_changes"][0]["operation"] == "create"
    assert not denied_target.exists()
    assert snapshot_workspace_files(workspace_root) == before_denied
    assert_success_artifact_identity(
        denied_result,
        workspace_id=workspace["workspace_id"],
    )

    denied_workflow = read_json(denied_result["workflow_artifact"])
    denied_audit = read_json(denied_result["audit_artifact"])
    denied_fix = read_json(denied_result["fix_artifact"])
    assert denied_workflow["edges"] == DEV_WORKFLOW_EDGES
    assert [node["depends_on"] for node in denied_workflow["nodes"]] == (
        DEV_WORKFLOW_DEPENDS_ON
    )
    assert all(
        node["kind"] not in {"parallel_worker", "agent_to_agent"}
        for node in denied_workflow["nodes"]
    )
    assert denied_workflow["nodes"][5]["auto_apply"] is False
    assert denied_result["node_results"]["audit"]["status"] == "warnings"
    assert denied_result["node_results"]["fix"]["status"] == "proposal_only"
    assert denied_audit["source_artifacts"]["workflow_artifact"] == (
        denied_result["workflow_artifact"]
    )
    assert any(
        finding["severity"] == "warning"
        and finding["code"] == "mutation_denied"
        and finding["path"] == str(denied_target.resolve())
        for finding in denied_audit["findings"]
    )
    assert denied_fix["status"] == "proposal_only"
    assert denied_fix["source_audit_artifact"] == denied_result["audit_artifact"]
    assert denied_fix["proposals"]
    assert all(proposal["auto_apply"] is False for proposal in denied_fix["proposals"])
    assert denied_result["fix_proposals"] == denied_fix["proposals"]

    assert workflow["edges"] == DEV_WORKFLOW_EDGES
    assert [node["depends_on"] for node in workflow["nodes"]] == DEV_WORKFLOW_DEPENDS_ON


def test_phase_one_background_run_defers_without_harness_execution(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    source_root = REPO_ROOT.resolve()
    workspace = add_workspace(runtime.home, workspace_root)
    target = workspace_root / "background-created.md"

    run = run_declared_bai(
        argv=[
            "run",
            "--workspace",
            workspace["workspace_id"],
            "--approve-mutation",
            str(target),
            "--background",
            f"propose-create {target}",
        ],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    result = load_json_output(run.stdout)
    scheduler = read_json(result["scheduler_artifact"])
    assert result["status"] == "deferred"
    assert result["background"] is True
    assert result["workspace_id"] == workspace["workspace_id"]
    assert "task_id" not in result
    assert scheduler["workspace_id"] == workspace["workspace_id"]
    assert scheduler["priority"] == "background"
    assert scheduler["lifecycle_status"] == "deferred"
    assert scheduler["execution_state"] == "deferred_metadata_only"
    assert scheduler["preemptible"] is True
    assert "harness_task_id" not in scheduler
    assert "run_status" not in scheduler
    assert "executed" not in scheduler
    assert not target.exists()
    assert_runtime_artifact(result["scheduler_artifact"], runtime, workspace_root, source_root)
    assert not (runtime.state / "events").exists()
    assert not (runtime.state / "workflows").exists()
    assert not (runtime.state / "context").exists()
    assert not (runtime.state / "approvals").exists()
    assert not (runtime.state / "audits").exists()
    assert not (runtime.state / "fixes").exists()
    assert not (runtime.memory / workspace["workspace_id"]).exists()

    run = run_declared_bai(
        argv=["run", "--workspace", workspace["workspace_id"], "plan only"],
        bai_home=runtime.home,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    foreground_result = load_json_output(run.stdout)
    assert_success_artifact_identity(
        foreground_result,
        workspace_id=workspace["workspace_id"],
    )
    foreground = read_json(foreground_result["scheduler_artifact"])
    background_after = read_json(result["scheduler_artifact"])
    assert foreground["priority"] == "foreground"
    assert foreground["lifecycle_status"] == "completed"
    assert foreground["execution_state"] == "foreground_run_completed"
    assert foreground["preempts_background"] is True
    assert foreground["preempted_background_task_ids"] == [scheduler["task_id"]]
    assert background_after["lifecycle_status"] == "preemptible_deferred"
    assert foreground_result["memory_artifacts"]
