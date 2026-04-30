from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from bai.core.runtime import RuntimePaths
from conftest import assert_not_under, assert_under, load_json_output, run_cli


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def add_workspace(capsys: pytest.CaptureFixture[str], workspace_root: Path) -> dict:
    code, output, error = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert code == 0
    assert error == ""
    return load_json_output(output)


def allow_test(
    capsys: pytest.CaptureFixture[str],
    workspace: str,
    argv: list[str],
    writable_path: Path | None = None,
) -> dict:
    command = ["workspace", "allow-test", workspace, "--argv", json.dumps(argv)]
    if writable_path is not None:
        command.extend(["--writable-path", str(writable_path)])
    code, output, error = run_cli(capsys, command)
    assert code == 0
    assert error == ""
    return load_json_output(output)


def test_cli_workspace_allow_test_persists_narrow_policy(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    argv = [sys.executable, "-c", "print('ok')"]
    writable = workspace_root / ".pytest_cache"

    updated = allow_test(capsys, workspace["workspace_id"], argv, writable)

    policy = updated["command_policy"]
    assert policy["test"]["allowed"] is True
    assert policy["test"]["allowed_argv"] == [argv]
    assert policy["test"]["allowed_prefixes"] == []
    assert policy["test"]["declared_writable_paths"] == [str(writable.resolve())]
    assert policy["test"]["write_enforcement"] == "trusted_command_no_sandbox"
    assert policy["inspect"] == workspace["command_policy"]["inspect"]
    assert policy["mutate"] == workspace["command_policy"]["mutate"]
    assert policy["workspace_network"] == workspace["command_policy"]["workspace_network"]
    assert policy["destructive"] == workspace["command_policy"]["destructive"]
    persisted = runtime.config / "workspaces" / f"{workspace['workspace_id']}.json"
    assert read_json(persisted)["command_policy"] == policy


def test_cli_workspace_allow_test_rejects_missing_workspace_and_bad_writable_path(
    runtime: RuntimePaths,
    workspace_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = runtime
    argv = [sys.executable, "-c", "print('ok')"]
    outside = tmp_path / "outside-cache"

    missing_code, missing_output, missing_error = run_cli(
        capsys,
        ["workspace", "allow-test", "missing", "--argv", json.dumps(argv)],
    )
    assert missing_code == 2
    assert missing_output == ""
    assert "workspace name index missing" in missing_error

    workspace = add_workspace(capsys, workspace_root)
    bad_code, bad_output, bad_error = run_cli(
        capsys,
        [
            "workspace",
            "allow-test",
            workspace["workspace_id"],
            "--argv",
            json.dumps(argv),
            "--writable-path",
            str(outside),
        ],
    )
    assert bad_code == 2
    assert bad_output == ""
    assert "writable path outside workspace" in bad_error


def test_cli_successful_test_run_records_artifacts_and_workflow_node(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    argv = [sys.executable, "-c", "import pathlib; print(pathlib.Path.cwd().name)"]
    allow_test(capsys, workspace["workspace_id"], argv, workspace_root / ".pytest_cache")

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], f"test {shlex.join(argv)}"],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    assert result["test_runs"] == [
        {
            "argv": argv,
            "cwd": str(workspace_root.resolve()),
            "exit_code": 0,
            "stdout_preview": f"{workspace_root.name}\n",
            "stderr_preview": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "declared_writable_paths": [],
            "write_enforcement": "trusted_command_no_sandbox",
        }
    ]
    workflow = read_json(result["workflow_artifact"])
    assert [node["kind"] for node in workflow["nodes"]] == [
        "plan",
        "test",
        "memory_write",
    ]
    assert workflow["artifacts"]["test_runs"] == result["test_runs"]
    events = [read_json(path)["event_type"] for path in result["event_artifacts"]]
    assert events == ["started", "completed"]
    for artifact in [
        result["context_artifact"],
        result["workflow_artifact"],
        *result["approval_artifacts"],
        *result["event_artifacts"],
        *result["memory_artifacts"],
    ]:
        assert_under(artifact, runtime.home)
        assert_not_under(artifact, workspace_root)


def test_cli_test_run_reports_trusted_direct_execution_not_write_sandbox(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    target = workspace_root / "README.md"
    argv = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('README.md').write_text('changed\\n')",
    ]
    allow_test(capsys, workspace["workspace_id"], argv)

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], f"test {shlex.join(argv)}"],
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    assert target.read_text() == "changed\n"
    assert result["test_runs"][0]["write_enforcement"] == "trusted_command_no_sandbox"
    assert result["test_runs"][0]["declared_writable_paths"] == []


def test_bare_cli_test_routes_through_harness(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = runtime
    workspace = add_workspace(capsys, workspace_root)
    argv = [sys.executable, "-c", "print('bare ok')"]
    allow_test(capsys, workspace["workspace_id"], argv)

    code, output, error = run_cli(
        capsys, ["--workspace", workspace["workspace_id"], f"test {shlex.join(argv)}"]
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    assert result["test_runs"][0]["stdout_preview"] == "bare ok\n"


def test_cli_default_denied_test_records_failed_event_without_memory(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    argv = [sys.executable, "-c", "print('should not run')"]

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], f"test {shlex.join(argv)}"],
    )

    assert code == 2
    assert output == ""
    assert "test command policy denies execution" in error
    events = [
        read_json(path)["event_type"]
        for path in sorted((runtime.state / "events").glob("*/*.json"))
    ]
    assert events == ["started", "failed"]
    assert not (runtime.memory / workspace["workspace_id"]).exists()


def test_cli_disallowed_test_argv_is_not_executed(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    allowed = [sys.executable, "-c", "print('allowed')"]
    target = workspace_root / "should-not-exist.txt"
    disallowed = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(target)!r}).write_text('bad')",
    ]
    allow_test(capsys, workspace["workspace_id"], allowed)

    code, output, error = run_cli(
        capsys,
        [
            "run",
            "--workspace",
            workspace["workspace_id"],
            f"test {shlex.join(disallowed)}",
        ],
    )

    assert code == 2
    assert output == ""
    assert "argv not approved" in error
    assert not target.exists()
    assert not (runtime.memory / workspace["workspace_id"]).exists()


def test_cli_nonzero_test_exit_records_failed_event_without_memory(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    argv = [sys.executable, "-c", "import sys; sys.stderr.write('bad\\n'); sys.exit(3)"]
    allow_test(capsys, workspace["workspace_id"], argv)

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], f"test {shlex.join(argv)}"],
    )

    assert code == 2
    assert output == ""
    assert "test command failed with exit code 3" in error
    events = [
        read_json(path)["event_type"]
        for path in sorted((runtime.state / "events").glob("*/*.json"))
    ]
    assert events == ["started", "failed"]
    assert not (runtime.memory / workspace["workspace_id"]).exists()
