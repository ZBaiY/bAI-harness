"""Success-path finalization for events, audit/fix, memory, and workflow state.

This module keeps Harness from owning all terminal ordering details. It writes
terminal evidence before success memory and ensures unused workflow permits or
partial memory artifacts are cleaned up if finalization fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts.audit import AuditStore, build_audit_result
from ..artifacts.fix import FixStore, build_fix_proposals
from ..artifacts.memory import MemoryStore
from ..artifacts.task_events import TaskEventStore
from ..artifacts.workflow import (
    WorkflowStore,
    build_bug_fix_optic_trace,
    build_phase_one_workflow,
)
from ..config.workspace import WorkspaceRecord


def finalize_success(
    *,
    workspace: WorkspaceRecord,
    task_id: str,
    request: str,
    execution_policy: str,
    workflow_mode: str,
    agent_output: dict[str, Any],
    approval_path: Path,
    approval_paths: list[Path],
    mutation_approval_paths: list[Path],
    context_path: Path,
    planned_workflow_path: Path,
    event_paths: list[Path],
    inspections: list[dict[str, Any]],
    test_runs: list[dict[str, Any]],
    applied_changes: list[dict[str, str]],
    denied_changes: list[dict[str, str]],
    node_results: dict[str, Any],
    events: TaskEventStore,
    audits: AuditStore,
    fixes: FixStore,
    memory: MemoryStore,
    workflows: WorkflowStore,
) -> dict[str, Any]:
    workflow_status = "completed_with_denials" if denied_changes else "completed"
    event_paths = list(event_paths)
    # Completed event is durable before audit/fix so those artifacts can cite it.
    # This keeps audit source_artifacts aligned with the terminal workflow status.
    event_paths.append(
        events.write(
            workspace=workspace,
            task_id=task_id,
            event_type="completed",
            reason="run completed",
            checkpoint_ref=str(approval_path),
        )
    )

    audit_path: Path | None = None
    fix_path: Path | None = None
    audit_findings = node_results["audit"]["findings"]
    fix_proposals = node_results["fix"]["proposals"]
    if workflow_mode == "dev":
        # Audit and fix are deterministic artifacts derived only from current-run metadata.
        # They are written before memory so a failed audit/fix write cannot leave success
        # memory for a run that did not fully finalize.
        audit_result = build_audit_result(
            agent_output=agent_output,
            applied_changes=applied_changes,
            denied_changes=denied_changes,
            inspections=inspections,
            test_runs=test_runs,
            workflow_status=workflow_status,
        )
        audit_path = audits.write(
            workspace=workspace,
            task_id=task_id,
            audit_result=audit_result,
            source_artifacts=audit_source_artifacts(
                context_path=context_path,
                approval_paths=approval_paths,
                event_paths=event_paths,
                mutation_approval_paths=mutation_approval_paths,
                workflow_path=planned_workflow_path,
            ),
        )
        audit_findings = audit_result["findings"]
        fix_proposals = build_fix_proposals(audit_findings)
        if fix_proposals:
            fix_path = fixes.write(
                workspace=workspace,
                task_id=task_id,
                proposals=fix_proposals,
                source_audit_artifact=str(audit_path),
            )
        apply_audit_result(
            node_results=node_results,
            audit_result=audit_result,
            fix_proposals=fix_proposals,
            fix_path=fix_path,
        )

    optic_trace_path: Path | None = None
    if workflow_mode == "dev":
        optic_trace_path = workflows.optic_trace_path(task_id)
        workflows.write_optic_trace(
            workspace=workspace,
            task_id=task_id,
            trace=build_bug_fix_optic_trace(
                task_id=task_id,
                workspace=workspace,
                workflow_status=workflow_status,
                node_results=node_results,
                context_path=context_path,
                approval_paths=approval_paths,
                event_paths=event_paths,
                audit_path=audit_path,
                fix_path=fix_path,
                optic_trace_path=optic_trace_path,
                applied_changes=applied_changes,
                mutation_approval_paths=mutation_approval_paths,
            ),
        )

    checked_workflow_path = workflows.check_write_allowed(
        workspace=workspace,
        task_id=task_id,
    )
    memory_paths: list[Path] = []
    try:
        # Memory is intentionally late: all terminal non-memory artifacts are durable first.
        # The final workflow path has been policy-prechecked, but the workflow is written only
        # after memory paths are known so it can bind the exact artifacts returned to callers.
        memory_path = memory.write(
            workspace=workspace,
            kind="session",
            content={"request": request, "plan_id": agent_output["id"]},
        )
        memory_paths.append(memory_path)
        working_memory_path = memory.write(
            workspace=workspace,
            kind="working",
            content={
                "plan_id": agent_output["id"],
                "task_id": task_id,
                "workspace_id": workspace.workspace_id,
                "workflow_mode": workflow_mode,
                "risks": agent_output.get("risks", []),
                "requested_approval_scope": agent_output.get(
                    "requested_approval_scope", []
                ),
                "workflow_artifact": str(checked_workflow_path),
                "approval_artifacts": [str(path) for path in approval_paths],
                "audit_artifact": str(audit_path) if audit_path else None,
                "audit_findings": audit_findings,
                "fix_artifact": str(fix_path) if fix_path else None,
                "fix_proposals": fix_proposals,
            },
        )
        memory_paths.append(working_memory_path)
        workflow_path = workflows.write(
            workspace=workspace,
            task_id=task_id,
            workflow=build_phase_one_workflow(
                workspace=workspace,
                execution_policy=execution_policy,
                agent_output=agent_output,
                context_path=context_path,
                approval_paths=approval_paths,
                mutation_approval_paths=mutation_approval_paths,
                event_paths=event_paths,
                memory_paths=memory_paths,
                inspections=inspections,
                test_runs=test_runs,
                applied_changes=applied_changes,
                denied_changes=denied_changes,
                status=workflow_status,
                audit_path=audit_path,
                fix_path=fix_path,
                node_results=node_results,
                audit_findings=audit_findings,
                fix_proposals=fix_proposals,
                optic_trace_path=optic_trace_path,
            ),
            checked_path=checked_workflow_path,
        )
    except Exception as exc:
        # A failed finalization must not leave a reusable workflow permit or success memory.
        # The original exception is re-raised; cleanup failures are attached as diagnostic
        # notes instead of replacing the primary failure.
        workflows.discard_write_permit(
            workspace=workspace,
            task_id=task_id,
            path=checked_workflow_path,
        )
        cleanup_memory_artifacts(
            memory_paths,
            workspace_memory_dir=memory.runtime.memory / workspace.workspace_id,
            exc=exc,
        )
        raise

    return {
        "workflow_status": workflow_status,
        "workflow_path": workflow_path,
        "memory_path": memory_path,
        "working_memory_path": working_memory_path,
        "memory_paths": memory_paths,
        "audit_path": audit_path,
        "fix_path": fix_path,
        "optic_trace_path": optic_trace_path,
        "event_paths": event_paths,
        "audit_findings": audit_findings,
        "fix_proposals": fix_proposals,
        "node_results": node_results,
    }


def apply_audit_result(
    *,
    node_results: dict[str, Any],
    audit_result: dict[str, Any],
    fix_proposals: list[dict[str, Any]],
    fix_path: Path | None,
) -> None:
    node_results["audit"]["status"] = audit_result["status"]
    node_results["audit"]["findings"] = audit_result["findings"]
    node_results["fix"]["proposals"] = fix_proposals
    node_results["fix"]["status"] = "proposal_only" if fix_proposals else "skipped"
    if fix_path is not None:
        node_results["fix"]["artifact"] = str(fix_path)


def audit_source_artifacts(
    *,
    context_path: Path,
    approval_paths: list[Path],
    event_paths: list[Path],
    mutation_approval_paths: list[Path],
    workflow_path: Path | None,
) -> dict[str, Any]:
    source_artifacts: dict[str, Any] = {
        "context_artifact": str(context_path),
        "approval_artifacts": [str(path) for path in approval_paths],
        "event_artifacts": [str(path) for path in event_paths],
        "mutation_approval_artifacts": [
            str(path) for path in mutation_approval_paths
        ],
    }
    if workflow_path is not None:
        source_artifacts["workflow_artifact"] = str(workflow_path)
    return source_artifacts


def cleanup_memory_artifacts(
    memory_paths: list[Path],
    *,
    workspace_memory_dir: Path,
    exc: BaseException,
) -> None:
    for path in reversed(memory_paths):
        try:
            path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            exc.add_note(f"memory artifact cleanup failed for {path}: {cleanup_exc}")
    cleanup_dirs = {path.parent for path in memory_paths}
    cleanup_dirs.add(workspace_memory_dir)
    for directory in sorted(
        cleanup_dirs,
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
