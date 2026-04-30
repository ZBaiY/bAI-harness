from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bai.cli import main
from bai.core.runtime import RuntimePaths


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimePaths:
    home = tmp_path / ".bai"
    monkeypatch.setenv("BAI_HOME", str(home))
    return RuntimePaths.discover()


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "README.md").write_text("# workspace\n")
    return root


def run_cli(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def load_json_output(output: str) -> Any:
    return json.loads(output)


def assert_under(path: str | Path, parent: Path) -> None:
    assert Path(path).resolve().is_relative_to(parent.resolve())


def assert_not_under(path: str | Path, parent: Path) -> None:
    assert not Path(path).resolve().is_relative_to(parent.resolve())
