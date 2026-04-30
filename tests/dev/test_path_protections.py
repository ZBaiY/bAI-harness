from __future__ import annotations

import os
from pathlib import Path

import pytest

from bai.execution.harness import Harness
from bai.core.runtime import RuntimePaths
from bai.config.workspace import WorkspaceStore


def test_path_traversal_mutation_escape_is_rejected_or_skipped(
    runtime: RuntimePaths,
    workspace_root: Path,
) -> None:
    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    escaped = workspace_root / ".." / "escaped.md"

    result = Harness(runtime=runtime, workspaces=store).run(
        workspace_ref=record.workspace_id,
        request=f"propose-create {escaped}",
    )

    assert result["applied_changes"] == []
    assert not escaped.resolve().exists()


def test_symlink_escape_mutation_is_rejected_when_supported(
    runtime: RuntimePaths,
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    link = workspace_root / "outside_link"
    try:
        os.symlink(outside_dir, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")

    store = WorkspaceStore(runtime)
    record = store.add("demo", workspace_root)
    target = link / "escaped.md"

    with pytest.raises(PermissionError):
        Harness(runtime=runtime, workspaces=store).run(
            workspace_ref=record.workspace_id,
            request=f"propose-create {target}",
            approved_mutation_paths=[str(target)],
        )
    assert not (outside_dir / "escaped.md").exists()
