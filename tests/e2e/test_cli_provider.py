from __future__ import annotations

import json
from pathlib import Path

import pytest

from bai.core.runtime import RuntimePaths
from conftest import assert_not_under, assert_under, load_json_output, run_cli


def test_cli_provider_show_uses_default_without_writing_config(
    runtime: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output, error = run_cli(capsys, ["provider", "show"])

    assert code == 0
    assert error == ""
    config = load_json_output(output)
    assert config == {
        "providers": {
            "local": {
                "provider": "local",
                "model": "local-plan-stub",
                "endpoint": "stub://local/plan",
                "network_required": False,
            }
        }
    }
    assert not (runtime.config / "providers.json").exists()


def test_cli_provider_set_local_persists_under_bai_home(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output, error = run_cli(
        capsys,
        [
            "provider",
            "set-local",
            "--model",
            "local-custom",
            "--endpoint",
            "stub://local/custom",
        ],
    )

    assert code == 0
    assert error == ""
    config = load_json_output(output)
    assert config["providers"]["local"]["model"] == "local-custom"
    path = runtime.config / "providers.json"
    assert path.exists()
    assert json.loads(path.read_text()) == config
    assert_under(path, runtime.home)
    assert_not_under(path, workspace_root)


def test_cli_provider_set_local_rejects_network_endpoint(
    runtime: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output, error = run_cli(
        capsys,
        [
            "provider",
            "set-local",
            "--model",
            "bad",
            "--endpoint",
            "https://example.com/model",
        ],
    )

    assert code == 2
    assert output == ""
    assert "local endpoint must be local" in error
    assert not (runtime.config / "providers.json").exists()


def test_harness_uses_configured_local_provider_from_bai_home(
    runtime: RuntimePaths,
    workspace_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_code, _, set_error = run_cli(
        capsys,
        [
            "provider",
            "set-local",
            "--model",
            "local-custom",
            "--endpoint",
            "stub://local/custom",
        ],
    )
    assert set_code == 0
    assert set_error == ""
    add_code, add_output, add_error = run_cli(
        capsys, ["workspace", "add", "demo", str(workspace_root)]
    )
    assert add_code == 0
    assert add_error == ""
    workspace = load_json_output(add_output)

    run_code, run_output, run_error = run_cli(
        capsys, ["run", "--workspace", workspace["workspace_id"], "plan only"]
    )

    assert run_code == 0
    assert run_error == ""
    result = load_json_output(run_output)
    assert result["model"] == {
        "provider": "local",
        "model": "local-custom",
        "endpoint": "stub://local/custom",
        "network_required": False,
    }
