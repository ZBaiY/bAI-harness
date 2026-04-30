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


def build_fix_proposals(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for finding in findings:
        code = finding.get("code")
        if code == "mutation_denied":
            proposal = {
                "status": "proposal_only",
                "kind": "request_mutation_approval",
                "message": "request approval for denied mutation path",
                "path": finding["path"],
                "operation": finding["operation"],
                "source_finding_code": "mutation_denied",
                "auto_apply": False,
            }
        elif code == "test_command_failed":
            proposal = {
                "status": "proposal_only",
                "kind": "manual_test_failure_review",
                "message": "inspect failing test output and update code manually",
                "source_finding_code": "test_command_failed",
                "auto_apply": False,
            }
        else:
            continue
        key = (
            str(proposal["source_finding_code"]),
            str(proposal.get("path")) if proposal.get("path") else None,
            str(proposal.get("operation")) if proposal.get("operation") else None,
        )
        if key in seen:
            continue
        seen.add(key)
        proposals.append(proposal)
    return proposals


class FixStore:
    def __init__(
        self,
        runtime: RuntimePaths | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.runtime = runtime or RuntimePaths.discover()
        self.policy = policy or PolicyEngine()

    def write(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        proposals: list[dict[str, Any]],
        source_audit_artifact: str,
    ) -> Path:
        path = self.runtime.state / "fixes" / f"{task_id}.json"
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "fix_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fix_id": f"fix-{uuid.uuid4().hex[:12]}",
            "task_id": task_id,
            "workspace_id": workspace.workspace_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "proposal_only",
            "proposals": proposals,
            "source_audit_artifact": source_audit_artifact,
        }
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path
