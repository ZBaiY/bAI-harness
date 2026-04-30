from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.core.runtime import RuntimePaths

from conftest import load_json_output, run_cli


@pytest.mark.parametrize("bad_path_name", ["missing_dir", "file.txt"])
def test_cli_workspace_add_rejects_invalid_root_before_config_write(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
    bad_path_name: str,
) -> None:
    bad_path = workspace_root.parent / bad_path_name
    if bad_path_name == "file.txt":
        bad_path.write_text("not a directory\n")

    code, output, error = run_cli(capsys, ["workspace", "add", "bad", str(bad_path)])

    assert code == 2
    assert output == ""
    assert "bai:" in error
    assert not (runtime.config / "workspaces").exists()


def test_cli_workspace_lifecycle_outputs_json_and_persists_config(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_code, add_output, add_error = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    assert add_error == ""
    added = load_json_output(add_output)
    assert added["name"] == "demo"
    assert added["root_path"] == str(workspace_root.resolve())
    assert added["allowed_paths"] == [str(workspace_root.resolve())]

    list_code, list_output, list_error = run_cli(capsys, ["workspace", "list"])
    assert list_code == 0
    assert list_error == ""
    listed = load_json_output(list_output)
    assert [item["workspace_id"] for item in listed] == [added["workspace_id"]]

    show_code, show_output, show_error = run_cli(
        capsys, ["workspace", "show", added["workspace_id"]]
    )
    assert show_code == 0
    assert show_error == ""
    shown = load_json_output(show_output)
    assert shown == added

    persisted = runtime.config / "workspaces" / f"{added['workspace_id']}.json"
    assert persisted.exists()
    assert json.loads(persisted.read_text()) == added


def test_cli_workspace_show_rejects_ambiguous_name(
    runtime: RuntimePaths,
    workspace_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    second_root = tmp_path / "second-workspace"
    second_root.mkdir()

    assert run_cli(capsys, ["workspace", "add", "demo", str(workspace_root)])[0] == 0
    assert run_cli(capsys, ["workspace", "add", "demo", str(second_root)])[0] == 0

    code, output, error = run_cli(capsys, ["workspace", "show", "demo"])

    assert code == 2
    assert output == ""
    assert "ambiguous" in error


def test_cli_workspace_show_missing_name_index_has_recovery_guidance(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_code, add_output, _ = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    added = load_json_output(add_output)
    for path in (runtime.config / "workspace_names").glob("*.json"):
        path.unlink()

    code, output, error = run_cli(capsys, ["workspace", "show", "demo"])

    assert code == 2
    assert output == ""
    assert "use workspace id or re-add workspace" in error

    id_code, id_output, id_error = run_cli(
        capsys, ["workspace", "show", added["workspace_id"]]
    )
    assert id_code == 0
    assert id_error == ""
    assert load_json_output(id_output)["workspace_id"] == added["workspace_id"]
