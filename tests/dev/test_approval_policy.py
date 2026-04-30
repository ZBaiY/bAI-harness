from __future__ import annotations

from pathlib import Path

from bai.core.policy import PolicyEngine
from bai.core.runtime import RuntimePaths
from bai.config.workspace import WorkspaceStore


def test_policy_allows_approval_write_only_under_runtime_approvals(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)
    policy = PolicyEngine(store)

    allowed = policy.gate_effect(
        workspace=workspace,
        effect={
            "kind": "approval_write",
            "path": str(runtime.state / "approvals" / "approval.json"),
        },
    )
    workspace_path = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "approval_write", "path": str(workspace_root / "approval.json")},
    )
    source_path = policy.gate_effect(
        workspace=workspace,
        effect={"kind": "approval_write", "path": str(Path.cwd() / "approval.json")},
    )

    assert allowed.allowed is True
    assert workspace_path.allowed is False
    assert source_path.allowed is False


def test_unknown_approval_like_effect_kind_remains_rejected(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = WorkspaceStore(runtime)
    workspace = store.add("demo", workspace_root)

    decision = PolicyEngine(store).gate_effect(
        workspace=workspace,
        effect={
            "kind": "approval_write_surprise",
            "path": str(runtime.state / "approvals" / "approval.json"),
        },
    )

    assert decision.allowed is False
    assert "unknown effect kind" in decision.reason
