"""Workflow DAG artifact builders and persistence for phase-one runs.

The workflow artifact records the serial DAG contract that Harness actually
orchestrates. It is not a workflow engine: no scheduler, agent loop, retry
logic, or parallel execution is implemented here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config.workspace import WorkspaceRecord
from ..core.io import atomic_write_text
from ..core.policy import PolicyEngine
from ..core.runtime import RuntimePaths
from ..optic_category import build_bug_fix_structural_loop_registry


def _resolve_workspace_path(workspace: WorkspaceRecord, path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(workspace.root_path) / candidate
    return candidate.resolve()


def build_phase_one_workflow(
    *,
    workspace: WorkspaceRecord,
    execution_policy: str,
    agent_output: dict[str, Any],
    context_path: Path,
    approval_paths: list[Path],
    mutation_approval_paths: list[Path],
    event_paths: list[Path],
    memory_paths: list[Path],
    inspections: list[dict[str, Any]],
    test_runs: list[dict[str, Any]],
    applied_changes: list[dict[str, str]],
    denied_changes: list[dict[str, str]],
    status: str,
    audit_path: Path | None = None,
    fix_path: Path | None = None,
    node_results: dict[str, Any] | None = None,
    audit_findings: list[dict[str, Any]] | None = None,
    fix_proposals: list[dict[str, Any]] | None = None,
    optic_trace_path: Path | None = None,
) -> dict[str, Any]:
    if agent_output.get("workflow_mode") == "dev":
        return build_developer_workflow(
            execution_policy=execution_policy,
            agent_output=agent_output,
            context_path=context_path,
            approval_paths=approval_paths,
            event_paths=event_paths,
            memory_paths=memory_paths,
            inspections=inspections,
            test_runs=test_runs,
            applied_changes=applied_changes,
            denied_changes=denied_changes,
            status=status,
            audit_path=audit_path,
            fix_path=fix_path,
            node_results=node_results or developer_node_results(
                agent_output=agent_output,
                test_runs=test_runs,
                applied_changes=applied_changes,
                denied_changes=denied_changes,
            ),
            audit_findings=audit_findings,
            fix_proposals=fix_proposals,
            optic_trace_path=optic_trace_path,
        )
    nodes: list[dict[str, Any]] = [
        {
            "id": "plan",
            "kind": "plan",
            "depends_on": [],
            "can_mutate": False,
            "approval_required": False,
            "agent_output_id": agent_output["id"],
        }
    ]
    edges: list[dict[str, str]] = []
    previous = "plan"

    for index, effect in enumerate(agent_output.get("proposed_effects", []), start=1):
        if effect.get("kind") != "inspect_file":
            continue
        node_id = "inspect" if index == 1 else f"inspect_{index}"
        resolved_path = _resolve_workspace_path(workspace, effect["path"])
        nodes.append(
            {
                "id": node_id,
                "kind": "inspect",
                "depends_on": [previous],
                "can_mutate": False,
                "approval_required": False,
                "effect": {"kind": "inspect_file", "path": str(resolved_path)},
            }
        )
        edges.append({"from": previous, "to": node_id})
        previous = node_id

    for index, effect in enumerate(agent_output.get("proposed_effects", []), start=1):
        if effect.get("kind") != "test_command":
            continue
        node_id = "test" if index == 1 else f"test_{index}"
        nodes.append(
            {
                "id": node_id,
                "kind": "test",
                "depends_on": [previous],
                "can_mutate": False,
                "approval_required": False,
                "effect": {
                    "kind": "test_command",
                    "argv": effect["argv"],
                    "declared_writable_paths": effect.get(
                        "declared_writable_paths", effect.get("writable_paths", [])
                    ),
                    "write_enforcement": "trusted_command_no_sandbox",
                },
            }
        )
        edges.append({"from": previous, "to": node_id})
        previous = node_id

    for index, change in enumerate(applied_changes, start=1):
        node_id = "code_mutation" if index == 1 else f"code_mutation_{index}"
        approval_artifact = (
            str(mutation_approval_paths[index - 1])
            if index - 1 < len(mutation_approval_paths)
            else None
        )
        nodes.append(
            {
                "id": node_id,
                "kind": "code_mutation",
                "depends_on": [previous],
                "can_mutate": True,
                "approval_required": True,
                "approval_artifact": approval_artifact,
                "effect": {
                    "kind": "code_mutation",
                    "path": change["path"],
                    "operation": change["operation"],
                },
            }
        )
        edges.append({"from": previous, "to": node_id})
        previous = node_id

    nodes.append(
        {
            "id": "memory_write",
            "kind": "memory_write",
            "depends_on": [previous],
            "can_mutate": False,
            "approval_required": False,
        }
    )
    edges.append({"from": previous, "to": "memory_write"})

    return {
        "execution_policy": execution_policy,
        "nodes": nodes,
        "edges": edges,
        "artifacts": {
            "context_artifact": str(context_path),
            "approval_artifacts": [str(path) for path in approval_paths],
            "event_artifacts": [str(path) for path in event_paths],
            "memory_artifacts": [str(path) for path in memory_paths],
            "inspections": inspections,
            "test_runs": test_runs,
            "applied_changes": applied_changes,
            "denied_changes": denied_changes,
        },
        "checkpoints": [str(path) for path in approval_paths],
        "status": status,
    }


def build_developer_workflow(
    *,
    execution_policy: str,
    agent_output: dict[str, Any],
    context_path: Path,
    approval_paths: list[Path],
    event_paths: list[Path],
    memory_paths: list[Path],
    inspections: list[dict[str, Any]],
    test_runs: list[dict[str, Any]],
    applied_changes: list[dict[str, str]],
    denied_changes: list[dict[str, str]],
    status: str,
    audit_path: Path | None,
    fix_path: Path | None,
    node_results: dict[str, Any],
    audit_findings: list[dict[str, Any]] | None = None,
    fix_proposals: list[dict[str, Any]] | None = None,
    optic_trace_path: Path | None = None,
) -> dict[str, Any]:
    findings = audit_findings if audit_findings is not None else node_results["audit"]["findings"]
    fixes = fix_proposals if fix_proposals is not None else node_results["fix"]["proposals"]
    nodes = [
        {
            "id": "plan",
            "kind": "plan",
            "depends_on": [],
            "can_mutate": False,
            "approval_required": False,
            "allowed_tools": [],
            "result": node_results["plan"],
        },
        {
            "id": "code",
            "kind": "code",
            "depends_on": ["plan"],
            "can_mutate": True,
            "approval_required": True,
            "allowed_tools": ["code_mutation"],
            "proposed_changes": agent_output.get("proposed_changes", []),
            "result": node_results["code"],
        },
        {
            "id": "doc",
            "kind": "doc",
            "depends_on": ["code"],
            "can_mutate": False,
            "approval_required": False,
            "allowed_tools": [],
            "result": node_results["doc"],
        },
        {
            "id": "test",
            "kind": "test",
            "depends_on": ["code"],
            "can_mutate": False,
            "approval_required": False,
            "allowed_tools": ["test_command"],
            "result": node_results["test"],
        },
        {
            "id": "audit",
            "kind": "audit",
            "depends_on": ["doc", "test"],
            "can_mutate": False,
            "approval_required": False,
            "allowed_tools": [],
            "result": node_results["audit"],
        },
        {
            "id": "fix",
            "kind": "fix",
            "depends_on": ["audit"],
            "can_mutate": False,
            "approval_required": True,
            "allowed_tools": [],
            "auto_apply": False,
            "result": node_results["fix"],
        },
    ]
    edges = [
        {"from": "plan", "to": "code"},
        {"from": "code", "to": "doc"},
        {"from": "code", "to": "test"},
        {"from": "doc", "to": "audit"},
        {"from": "test", "to": "audit"},
        {"from": "audit", "to": "fix"},
    ]
    artifacts = {
        "context_artifact": str(context_path),
        "approval_artifacts": [str(path) for path in approval_paths],
        "event_artifacts": [str(path) for path in event_paths],
        "memory_artifacts": [str(path) for path in memory_paths],
        "inspections": inspections,
        "test_runs": test_runs,
        "applied_changes": applied_changes,
        "denied_changes": denied_changes,
        "node_results": node_results,
        "audit_findings": findings,
        "fix_proposals": fixes,
    }
    if audit_path is not None:
        artifacts["audit_artifact"] = str(audit_path)
    if fix_path is not None:
        artifacts["fix_artifact"] = str(fix_path)
    if optic_trace_path is not None:
        artifacts["optic_trace_artifact"] = str(optic_trace_path)
    return {
        "workflow_mode": "dev",
        "execution_policy": execution_policy,
        "nodes": nodes,
        "edges": edges,
        "artifacts": artifacts,
        "checkpoints": [str(path) for path in approval_paths],
        "status": status,
    }


def build_bug_fix_optic_trace(
    *,
    task_id: str,
    workspace: WorkspaceRecord,
    workflow_status: str,
    node_results: dict[str, Any],
    context_path: Path,
    approval_paths: list[Path],
    event_paths: list[Path],
    audit_path: Path | None,
    fix_path: Path | None,
    optic_trace_path: Path,
    applied_changes: list[dict[str, str]],
    mutation_approval_paths: list[Path],
) -> dict[str, Any]:
    registry = build_bug_fix_structural_loop_registry()
    validated = registry.validate_registered("BugFixStructuralLoopOptic")
    return {
        "task_id": task_id,
        "workspace_id": workspace.workspace_id,
        "composition_id": validated.composition_id,
        "composition_validated": True,
        "workflow_status": workflow_status,
        "source_optics": list(validated.source_optics),
        "input_types": list(validated.input_types),
        "output_types": list(validated.output_types),
        "optional_output_types": list(validated.optional_output_types),
        "handoffs": [
            {
                "from_optic": handoff.from_optic,
                "output_type": handoff.output_type,
                "to_optic": handoff.to_optic,
                "input_type": handoff.input_type,
            }
            for handoff in validated.handoffs
        ],
        "role_steps": [
            _build_bug_fix_role_step(
                optic_id=optic_id,
                node_results=node_results,
                audit_path=audit_path,
                fix_path=fix_path,
                optic_trace_path=optic_trace_path,
                applied_changes=applied_changes,
                mutation_approval_paths=mutation_approval_paths,
            )
            for optic_id in validated.source_optics
        ],
        "source_artifacts": {
            "context_artifact": str(context_path),
            "approval_artifacts": [str(path) for path in approval_paths],
            "event_artifacts": [str(path) for path in event_paths],
            "audit_artifact": str(audit_path) if audit_path else None,
            "fix_artifact": str(fix_path) if fix_path else None,
        },
        "non_goals": {
            "autonomous_patch_applied": False,
            "agent_to_agent_chat": False,
            "parallel_execution": False,
        },
    }


def _build_bug_fix_role_step(
    *,
    optic_id: str,
    node_results: dict[str, Any],
    audit_path: Path | None,
    fix_path: Path | None,
    optic_trace_path: Path,
    applied_changes: list[dict[str, str]],
    mutation_approval_paths: list[Path],
) -> dict[str, Any]:
    optic = build_bug_fix_structural_loop_registry().optic(optic_id)
    return {
        "optic_id": optic_id,
        "input_types": list(optic.input_types),
        "output_types": list(optic.output_types),
        "optional_output_types": list(optic.optional_output_types),
        "read_projection": list(optic.read_projection),
        "write_surface": list(optic.write_surface),
        "lineage": list(optic.lineage_required),
        "status": _bug_fix_role_status(
            optic_id=optic_id,
            node_results=node_results,
            applied_changes=applied_changes,
            audit_path=audit_path,
            fix_path=fix_path,
        ),
        "written_artifacts": _bug_fix_role_written_artifacts(
            optic_id=optic_id,
            audit_path=audit_path,
            fix_path=fix_path,
            optic_trace_path=optic_trace_path,
        ),
        "mutation_evidence": _bug_fix_role_mutation_evidence(
            optic_id=optic_id,
            applied_changes=applied_changes,
            mutation_approval_paths=mutation_approval_paths,
        ),
    }


def _bug_fix_role_status(
    *,
    optic_id: str,
    node_results: dict[str, Any],
    applied_changes: list[dict[str, str]],
    audit_path: Path | None,
    fix_path: Path | None,
) -> str:
    if optic_id == "RoadmapExtractorOptic":
        return "completed"
    if optic_id == "AuditorOptic":
        return "completed" if audit_path is not None else "skipped"
    if optic_id == "PlannerOptic":
        return "proposal_only" if fix_path is not None else "completed"
    if optic_id == "PatcherOptic":
        if applied_changes:
            return "applied"
        if node_results.get("code", {}).get("status") in {"proposal_only", "denied"}:
            return str(node_results["code"]["status"])
        return "not_applied_no_patchdiff"
    if optic_id == "ReviewerOptic":
        if applied_changes:
            return "completed_structural"
        return "completed_structural_no_patchdiff"
    return "unknown"


def _bug_fix_role_written_artifacts(
    *,
    optic_id: str,
    audit_path: Path | None,
    fix_path: Path | None,
    optic_trace_path: Path,
) -> list[dict[str, str]]:
    if optic_id == "AuditorOptic" and audit_path is not None:
        return [{"kind": "audit_artifact", "path": str(audit_path)}]
    if optic_id == "PlannerOptic" and fix_path is not None:
        return [{"kind": "fix_artifact", "path": str(fix_path)}]
    return []


def _bug_fix_role_mutation_evidence(
    *,
    optic_id: str,
    applied_changes: list[dict[str, str]],
    mutation_approval_paths: list[Path],
) -> list[dict[str, str]]:
    if optic_id not in {"PatcherOptic", "ReviewerOptic"}:
        return []
    return [
        {
            "path": change["path"],
            "operation": change["operation"],
            "approval_artifact": str(mutation_approval_paths[index]),
        }
        for index, change in enumerate(applied_changes)
        if index < len(mutation_approval_paths)
    ]


def developer_node_results(
    *,
    agent_output: dict[str, Any],
    test_runs: list[dict[str, Any]],
    applied_changes: list[dict[str, str]],
    denied_changes: list[dict[str, str]],
    failure_reason: str | None = None,
) -> dict[str, Any]:
    proposed_changes = agent_output.get("proposed_changes", [])
    proposed_tests = [
        effect
        for effect in agent_output.get("proposed_effects", [])
        if effect.get("kind") == "test_command"
    ]
    findings: list[dict[str, Any]] = []
    if denied_changes:
        findings.append(
            {
                "severity": "warning",
                "message": "code mutation proposal was denied",
            }
        )
    if failure_reason and "test command" in failure_reason:
        findings.append({"severity": "error", "message": failure_reason})

    code_status = "skipped"
    if applied_changes:
        code_status = "completed"
    elif denied_changes:
        code_status = "denied"
    elif proposed_changes:
        code_status = "proposal_only"

    test_status = "skipped"
    if failure_reason and "test command" in failure_reason:
        test_status = "failed"
    elif test_runs:
        test_status = "completed"
    elif proposed_tests:
        test_status = "planned"

    audit_status = "completed" if not failure_reason else "skipped"
    fix_proposals = (
        [
            {
                "status": "proposal_only",
                "reason": "manual intervention required for audit findings",
            }
        ]
        if findings and not failure_reason
        else []
    )
    fix_status = "proposal_only" if fix_proposals else "skipped"

    return {
        "plan": {"status": "completed", "can_mutate": False},
        "code": {
            "status": code_status,
            "proposed_changes": proposed_changes,
            "applied_changes": applied_changes,
            "denied_changes": denied_changes,
        },
        "doc": {
            "status": "proposal_only",
            "proposal": "documentation update proposal only in phase one",
        },
        "test": {"status": test_status, "test_runs": test_runs},
        "audit": {
            "status": audit_status,
            "can_mutate": False,
            "findings": findings if not failure_reason else [],
        },
        "fix": {
            "status": fix_status,
            "auto_apply": False,
            "proposals": fix_proposals,
        },
    }


class WorkflowStore:
    def __init__(
        self,
        runtime: RuntimePaths | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.runtime = runtime or RuntimePaths.discover()
        self.policy = policy or PolicyEngine()
        self._checked_writes: set[tuple[str, str, Path]] = set()

    def write(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        workflow: dict[str, Any],
        checked_path: Path | None = None,
    ) -> Path:
        if checked_path is None:
            path = self._gate_write_path(workspace=workspace, task_id=task_id)
        else:
            # Checked writes consume a permit issued by check_write_allowed(). A raw Path is
            # not enough because that would let callers bypass the policy gate embedded in
            # the store boundary.
            path = Path(checked_path)
            expected_path = self._workflow_path(task_id)
            if path != expected_path:
                raise PermissionError("workflow write permit path mismatch")
            permit = (workspace.workspace_id, task_id, path)
            if permit not in self._checked_writes:
                raise PermissionError("workflow write permit missing")
            self._checked_writes.remove(permit)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "workspace_id": workspace.workspace_id,
            "created_at": workflow.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "workflow_mode": workflow.get("workflow_mode", "default"),
            "execution_policy": workflow["execution_policy"],
            "nodes": workflow["nodes"],
            "edges": workflow["edges"],
            "artifacts": workflow["artifacts"],
            "checkpoints": workflow["checkpoints"],
            "status": workflow["status"],
        }
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def check_write_allowed(self, *, workspace: WorkspaceRecord, task_id: str) -> Path:
        path = self._gate_write_path(workspace=workspace, task_id=task_id)
        # The permit lets finalization preflight policy before writing memory. It is one-use
        # and must be discarded if finalization fails before the workflow write consumes it.
        self._checked_writes.add((workspace.workspace_id, task_id, path))
        return path

    def optic_trace_path(self, task_id: str) -> Path:
        return self.runtime.state / "workflows" / "optic_traces" / f"{task_id}.json"

    def write_optic_trace(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        trace: dict[str, Any],
    ) -> Path:
        path = self.optic_trace_path(task_id)
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "workflow_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            **trace,
        }
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def discard_write_permit(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        path: Path,
    ) -> None:
        self._checked_writes.discard((workspace.workspace_id, task_id, Path(path)))

    def _gate_write_path(self, *, workspace: WorkspaceRecord, task_id: str) -> Path:
        path = self._workflow_path(task_id)
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "workflow_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return path

    def _workflow_path(self, task_id: str) -> Path:
        return self.runtime.state / "workflows" / f"{task_id}.json"
