"""Approval artifact persistence for phase-one checkpoints.

Approval records are runtime-state artifacts under BAI_HOME. They document that
Harness accepted an agent output or a specific mutation approval scope; they do
not represent task completion or successful effect execution.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config.workspace import WorkspaceRecord
from ..core.io import atomic_write_text
from ..core.policy import PolicyEngine
from ..core.runtime import RuntimePaths


class ApprovalStore:
    def __init__(
        self,
        runtime: RuntimePaths | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.runtime = runtime or RuntimePaths.discover()
        self.policy = policy or PolicyEngine()

    def write_agent_output_acceptance(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        agent_output_id: str,
        bound_to_artifacts: list[str],
        approval_scope: dict[str, Any],
    ) -> Path:
        return self._write(
            workspace=workspace,
            approval={
                "approval_id": f"approval-{uuid.uuid4().hex[:12]}",
                "kind": "agent_output_acceptance",
                "workspace_id": workspace.workspace_id,
                "task_id": task_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "agent_output_id": agent_output_id,
                "status": "accepted",
                "bound_to_artifacts": bound_to_artifacts,
                "approval_scope": approval_scope,
            },
        )

    def write_mutation_approval(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        agent_output_id: str,
        approved_path: str,
        operation: str,
        approving_mechanism: str,
        bound_to_artifacts: list[str],
        content_sha256: str,
        expected_preimage: dict[str, Any],
    ) -> Path:
        approval_scope = {
            "workspace_id": workspace.workspace_id,
            "approved_path": approved_path,
            "operation": operation,
            "approving_mechanism": approving_mechanism,
            "task_id": task_id,
            "content_sha256": content_sha256,
            "expected_preimage": expected_preimage,
        }
        return self._write(
            workspace=workspace,
            approval={
                "approval_id": f"approval-{uuid.uuid4().hex[:12]}",
                "kind": "mutation_approval",
                "workspace_id": workspace.workspace_id,
                "task_id": task_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "agent_output_id": agent_output_id,
                "status": "approved",
                "bound_to_artifacts": bound_to_artifacts,
                "approval_scope": approval_scope,
            },
        )

    def _write(self, *, workspace: WorkspaceRecord, approval: dict[str, Any]) -> Path:
        path = self.runtime.state / "approvals" / f"{approval['approval_id']}.json"
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "approval_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(approval, indent=2, sort_keys=True) + "\n")
        return path
