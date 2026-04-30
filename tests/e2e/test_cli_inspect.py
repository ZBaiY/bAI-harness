from __future__ import annotations

import os
from pathlib import Path

import pytest

from bai.core.runtime import RuntimePaths
from conftest import assert_under, load_json_output, run_cli


def add_workspace(capsys: pytest.CaptureFixture[str], workspace_root: Path) -> dict:
    code, output, error = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert code == 0
    assert error == ""
    return load_json_output(output)


def test_run_inspect_reads_relative_file_without_mutating_workspace(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (workspace_root / "README.md").write_text("alpha\nbeta\ngamma\n")
    workspace = add_workspace(capsys, workspace_root)
    before = {path.name: path.read_text() for path in workspace_root.iterdir() if path.is_file()}

    code, output, error = run_cli(
        capsys, ["run", "--workspace", workspace["name"], "inspect README.md"]
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    assert result["inspections"] == [
        {
            "path": str((workspace_root / "README.md").resolve()),
            "status": "success",
            "content_preview": "alpha\nbeta\ngamma\n",
            "truncated": False,
        }
    ]
    assert_under(result["approval_artifact"], runtime.state)
    assert_under(result["memory_artifact"], runtime.memory)
    assert Path(result["approval_artifact"]).exists()
    assert Path(result["memory_artifact"]).exists()
    assert {path.name: path.read_text() for path in workspace_root.iterdir() if path.is_file()} == before


def test_bare_cli_inspect_routes_through_harness(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (workspace_root / "README.md").write_text("bare inspect\n")
    workspace = add_workspace(capsys, workspace_root)

    code, output, error = run_cli(
        capsys, ["--workspace", workspace["workspace_id"], "inspect README.md"]
    )

    assert code == 0
    assert error == ""
    result = load_json_output(output)
    assert result["workspace_id"] == workspace["workspace_id"]
    assert result["inspections"][0]["content_preview"] == "bare inspect\n"
    assert Path(result["memory_artifact"]).is_relative_to(runtime.memory)


@pytest.mark.parametrize(
    "inspect_request",
    [
        "inspect ../outside.txt",
        None,
    ],
)
def test_inspect_rejects_paths_outside_workspace(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
    inspect_request: str | None,
) -> None:
    _ = runtime
    outside = workspace_root.parent / "outside.txt"
    outside.write_text("outside\n")
    workspace = add_workspace(capsys, workspace_root)
    actual_request = inspect_request or f"inspect {outside}"

    code, output, error = run_cli(
        capsys, ["run", "--workspace", workspace["workspace_id"], actual_request]
    )

    assert code == 2
    assert output == ""
    assert "outside allowed workspace paths" in error
    assert outside.read_text() == "outside\n"


def test_inspect_missing_file_fails_cleanly_without_workspace_artifacts(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = add_workspace(capsys, workspace_root)
    before = {path.name for path in workspace_root.iterdir()}

    code, output, error = run_cli(
        capsys, ["run", "--workspace", workspace["workspace_id"], "inspect missing.txt"]
    )

    assert code == 2
    assert output == ""
    assert "inspect target does not exist" in error
    assert {path.name for path in workspace_root.iterdir()} == before
    assert not (runtime.memory / workspace["workspace_id"]).exists()


def test_inspect_symlink_escape_is_rejected_when_supported(
    runtime: RuntimePaths,
    workspace_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = runtime
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("secret\n")
    link = workspace_root / "outside_link"
    try:
        os.symlink(outside_dir, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")
    workspace = add_workspace(capsys, workspace_root)

    code, output, error = run_cli(
        capsys,
        ["run", "--workspace", workspace["workspace_id"], "inspect outside_link/secret.txt"],
    )

    assert code == 2
    assert output == ""
    assert "outside allowed workspace paths" in error
