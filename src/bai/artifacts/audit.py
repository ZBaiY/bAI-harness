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


def build_audit_result(
    *,
    agent_output: dict[str, Any],
    applied_changes: list[dict[str, str]],
    denied_changes: list[dict[str, str]],
    inspections: list[dict[str, Any]],
    test_runs: list[dict[str, Any]],
    workflow_status: str,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if not test_runs and not _has_test_effect(agent_output) and not failure_reason:
        findings.append(
            {
                "severity": "info",
                "code": "no_test_command",
                "message": "no trusted test command ran",
            }
        )
    for denied in denied_changes:
        findings.append(
            {
                "severity": "warning",
                "code": "mutation_denied",
                "message": "code mutation proposal was denied",
                "path": denied["path"],
                "operation": denied["operation"],
                "reason": denied.get("reason", "mutation denied"),
            }
        )
    for run in test_runs:
        if run.get("write_enforcement") == "trusted_command_no_sandbox":
            findings.append(
                {
                    "severity": "info",
                    "code": "trusted_test_command_no_sandbox",
                    "message": "test command ran as trusted direct execution without a write sandbox",
                }
            )
        exit_code = run.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            findings.append(
                {
                    "severity": "error",
                    "code": "test_command_failed",
                    "message": f"test command failed with exit code {exit_code}",
                    "exit_code": exit_code,
                }
            )
    if (
        failure_reason
        and "test command failed with exit code" in failure_reason
        and not any(finding.get("code") == "test_command_failed" for finding in findings)
    ):
        findings.append(
            {
                "severity": "error",
                "code": "test_command_failed",
                "message": failure_reason,
            }
        )
    return {
        "status": _audit_status(findings=findings, workflow_status=workflow_status),
        "findings": findings,
    }


class AuditStore:
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
        audit_result: dict[str, Any],
        source_artifacts: dict[str, Any],
    ) -> Path:
        path = self.runtime.state / "audits" / f"{task_id}.json"
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "audit_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "audit_id": f"audit-{uuid.uuid4().hex[:12]}",
            "task_id": task_id,
            "workspace_id": workspace.workspace_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": audit_result["status"],
            "findings": audit_result["findings"],
            "source_artifacts": source_artifacts,
        }
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path


def _has_test_effect(agent_output: dict[str, Any]) -> bool:
    return any(
        effect.get("kind") == "test_command"
        for effect in agent_output.get("proposed_effects", [])
    )


def _audit_status(
    *, findings: list[dict[str, Any]], workflow_status: str
) -> str:
    if workflow_status == "failed" or any(
        finding.get("severity") == "error" for finding in findings
    ):
        return "failed"
    if any(finding.get("severity") == "warning" for finding in findings):
        return "warnings"
    if findings:
        return "passed_with_notes"
    return "passed"
