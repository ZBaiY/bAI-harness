"""Serial phase-one Harness orchestration for one bounded user request.

Harness is the lifecycle owner for a foreground run: resolve workspace, route
locally, invoke the stateless agent, gate output, execute narrow effects, and
delegate success/failure artifact finalization. It is not a workflow engine.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

from ..artifacts.approvals import ApprovalStore
from ..artifacts.audit import AuditStore, build_audit_result
from ..artifacts.context import ContextStore
from ..artifacts.fix import FixStore, build_fix_proposals
from ..artifacts.memory import MemoryStore
from ..artifacts.task_events import TaskEventStore
from ..artifacts.workflow import (
    WorkflowStore,
    build_bug_fix_optic_trace,
    build_phase_one_workflow,
    developer_node_results,
)
from ..config.router import Router
from ..config.workspace import WorkspaceRecord, WorkspaceStore
from ..core.errors import BaiUserError, HarnessBoundaryError
from ..core.policy import PolicyEngine, PolicyEngineInterface
from ..core.runtime import RuntimePaths
from .agent import PlanAgent
from .effects import EffectExecutor, INSPECTION_PREVIEW_CHARS, TestCommandRunError
from .finalize import apply_audit_result, audit_source_artifacts, finalize_success
from .test_command import TestCommandExecutor

EXPECTED_RUN_FAILURES = (
    HarnessBoundaryError,
    BaiUserError,
    FileNotFoundError,
    PermissionError,
)


class Harness:
    def __init__(
        self,
        *,
        runtime: RuntimePaths | None = None,
        workspaces: WorkspaceStore | None = None,
        policy: PolicyEngineInterface | None = None,
        router: Router | None = None,
        agent: PlanAgent | None = None,
        memory: MemoryStore | None = None,
        events: TaskEventStore | None = None,
        approvals: ApprovalStore | None = None,
        audits: AuditStore | None = None,
        fixes: FixStore | None = None,
        context: ContextStore | None = None,
        workflows: WorkflowStore | None = None,
        effects: EffectExecutor | None = None,
        test_executor: TestCommandExecutor | None = None,
    ) -> None:
        self.runtime = runtime or RuntimePaths.discover()
        self.workspaces = workspaces or WorkspaceStore(self.runtime)
        self.policy = policy or PolicyEngine(self.workspaces)
        self.router = router or Router()
        self.agent = agent or PlanAgent()
        self.memory = memory or MemoryStore(self.runtime, self.policy)  # type: ignore[arg-type]
        self.events = events or TaskEventStore(self.runtime, self.policy)  # type: ignore[arg-type]
        self.approvals = approvals or ApprovalStore(self.runtime, self.policy)  # type: ignore[arg-type]
        self.audits = audits or AuditStore(self.runtime, self.policy)  # type: ignore[arg-type]
        self.fixes = fixes or FixStore(self.runtime, self.policy)  # type: ignore[arg-type]
        self.context = context or ContextStore(self.runtime, self.policy)  # type: ignore[arg-type]
        self.workflows = workflows or WorkflowStore(self.runtime, self.policy)  # type: ignore[arg-type]
        self.effects = effects or EffectExecutor(
            runtime=self.runtime,
            workspaces=self.workspaces,
            policy=self.policy,
            approvals=self.approvals,
            test_executor=test_executor,
        )

    def run(
        self,
        *,
        workspace_ref: str,
        request: str,
        execution_policy: str = "serial",
        approved_mutation_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        self.runtime.ensure()
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        event_paths: list[Path] = []
        context_path: Path | None = None
        execution_agent_output: dict[str, Any] | None = None
        approval_paths: list[Path] = []
        mutation_approval_paths: list[Path] = []
        inspections: list[dict[str, Any]] = []
        test_runs: list[dict[str, Any]] = []
        applied_changes: list[dict[str, str]] = []
        denied_changes: list[dict[str, str]] = []
        audit_path: Path | None = None
        fix_path: Path | None = None
        if execution_policy != "serial":
            raise ValueError("phase one defaults to serial and accepts only serial execution")
        workspace = self.workspaces.get(workspace_ref)
        self.workspaces.validate(workspace)
        try:
            # The started event is the first durable run artifact after workspace validation.
            # Failures before this point have no trusted workspace/task context to attach to.
            event_paths.append(
                self.events.write(
                    workspace=workspace,
                    task_id=task_id,
                    event_type="started",
                    reason="run started",
                )
            )
            model_route = self.router.select(
                provider_allowance={"local": True, "workspace_network": False}
            )
            context_path = self.context.write(
                workspace=workspace,
                task_id=task_id,
                bundle={
                    "user_request": request,
                    "execution_policy": execution_policy,
                    "router_constraints": {
                        "local": True,
                        "workspace_network": False,
                    },
                    "approved_memory_snippets": [],
                },
            )
            agent_output = self.agent.run(
                request=request,
                workspace=workspace,
                execution_policy=execution_policy,
                model_route=model_route,
            )
            # Freeze separate views so policy validation cannot be bypassed by later mutation.
            # The policy copy is the contract that was accepted; the execution copy is what
            # downstream components read after that acceptance.
            policy_agent_output = copy.deepcopy(agent_output)
            execution_agent_output = copy.deepcopy(agent_output)
            workflow_mode = execution_agent_output.get("workflow_mode", "default")
            output_decision = self.policy.gate_agent_output(policy_agent_output)
            if not output_decision.allowed:
                raise PermissionError(output_decision.reason)
            # This approval records agent-output acceptance only. It must not be read as
            # task completion; completed/failed task events are the terminal lifecycle source.
            approval_path = self._record_agent_output_acceptance(
                workspace=workspace,
                task_id=task_id,
                agent_output=execution_agent_output,
            )
            approval_paths.append(approval_path)
            workflow_path = self.workflows.write(
                workspace=workspace,
                task_id=task_id,
                workflow=build_phase_one_workflow(
                    workspace=workspace,
                    execution_policy=execution_policy,
                    agent_output=execution_agent_output,
                    context_path=context_path,
                    approval_paths=approval_paths,
                    mutation_approval_paths=[],
                    event_paths=event_paths,
                    memory_paths=[],
                    inspections=[],
                    test_runs=[],
                    applied_changes=[],
                    denied_changes=[],
                    status="planned",
                ),
            )
            inspections = self.effects.execute_inspections(
                workspace=workspace,
                proposed_effects=execution_agent_output.get("proposed_effects", []),
            )
            # Dev workflow keeps code before tests to model plan->code->doc->test->audit->fix.
            # This is still serial execution: the DAG shape is an artifact contract, not fan-out.
            if workflow_mode == "dev":
                (
                    applied_changes,
                    mutation_approval_paths,
                    denied_changes,
                ) = self.effects.execute_approved_mutations(
                    workspace=workspace,
                    task_id=task_id,
                    agent_output_id=execution_agent_output["id"],
                    proposed_changes=execution_agent_output.get("proposed_changes", []),
                    approved_mutation_paths=approved_mutation_paths or [],
                    bound_to_artifacts=[str(approval_path)],
                )
                test_runs = self.effects.execute_tests(
                    workspace=workspace,
                    proposed_effects=execution_agent_output.get("proposed_effects", []),
                )
            else:
                test_runs = self.effects.execute_tests(
                    workspace=workspace,
                    proposed_effects=execution_agent_output.get("proposed_effects", []),
                )
                (
                    applied_changes,
                    mutation_approval_paths,
                    denied_changes,
                ) = self.effects.execute_approved_mutations(
                    workspace=workspace,
                    task_id=task_id,
                    agent_output_id=execution_agent_output["id"],
                    proposed_changes=execution_agent_output.get("proposed_changes", []),
                    approved_mutation_paths=approved_mutation_paths or [],
                    bound_to_artifacts=[str(approval_path)],
                )
            approval_paths.extend(mutation_approval_paths)
            node_results = developer_node_results(
                agent_output=execution_agent_output,
                test_runs=test_runs,
                applied_changes=applied_changes,
                denied_changes=denied_changes,
            )
            # Success finalization owns terminal event, audit/fix, memory, and final workflow order.
            # Keeping that sequence in one helper prevents memory from being committed before
            # the terminal workflow can bind it.
            finalized = finalize_success(
                workspace=workspace,
                task_id=task_id,
                request=request,
                execution_policy=execution_policy,
                workflow_mode=workflow_mode,
                agent_output=execution_agent_output,
                approval_path=approval_path,
                context_path=context_path,
                planned_workflow_path=workflow_path,
                approval_paths=approval_paths,
                mutation_approval_paths=mutation_approval_paths,
                event_paths=event_paths,
                inspections=inspections,
                test_runs=test_runs,
                applied_changes=applied_changes,
                denied_changes=denied_changes,
                node_results=node_results,
                events=self.events,
                audits=self.audits,
                fixes=self.fixes,
                memory=self.memory,
                workflows=self.workflows,
            )
            return {
                "status": "completed_with_denials" if denied_changes else "success",
                "task_id": task_id,
                "execution_policy": execution_policy,
                "workspace_id": workspace.workspace_id,
                "workflow_mode": workflow_mode,
                "model": execution_agent_output["model"],
                "plan": execution_agent_output,
                "workflow_artifact": str(finalized["workflow_path"]),
                "context_artifact": str(context_path),
                "approval_artifact": str(approval_path),
                "approval_artifacts": [str(path) for path in approval_paths],
                "memory_artifact": str(finalized["memory_path"]),
                "working_memory_artifact": str(finalized["working_memory_path"]),
                "memory_artifacts": [str(path) for path in finalized["memory_paths"]],
                "audit_artifact": (
                    str(finalized["audit_path"]) if finalized["audit_path"] else None
                ),
                "fix_artifact": (
                    str(finalized["fix_path"]) if finalized["fix_path"] else None
                ),
                "optic_trace_artifact": (
                    str(finalized["optic_trace_path"])
                    if finalized["optic_trace_path"]
                    else None
                ),
                "event_artifacts": [str(path) for path in finalized["event_paths"]],
                "applied_changes": applied_changes,
                "denied_changes": denied_changes,
                "inspections": inspections,
                "test_runs": test_runs,
                "node_results": finalized["node_results"],
                "audit_findings": finalized["audit_findings"],
                "fix_proposals": finalized["fix_proposals"],
            }
        except EXPECTED_RUN_FAILURES as exc:
            if isinstance(exc, TestCommandRunError):
                test_runs = list(exc.test_runs)
            try:
                # Expected run failures still get failed lifecycle evidence when possible.
                # Secondary artifact failures are attached as notes so the original user-facing
                # failure remains the exception that callers observe.
                event_paths.append(
                    self.events.write(
                        workspace=workspace,
                        task_id=task_id,
                        event_type="failed",
                        reason=str(exc),
                    )
                )
            except Exception as event_exc:
                exc.add_note(f"failed event write failed: {event_exc}")
            try:
                if (
                    execution_agent_output is not None
                    and context_path is not None
                    and approval_paths
                ):
                    terminal_approval_paths = list(approval_paths)
                    for path in mutation_approval_paths:
                        if path not in terminal_approval_paths:
                            terminal_approval_paths.append(path)
                    workflow_kwargs: dict[str, Any] = {}
                    if execution_agent_output.get("workflow_mode") == "dev":
                        audit_result = build_audit_result(
                            agent_output=execution_agent_output,
                            applied_changes=applied_changes,
                            denied_changes=denied_changes,
                            inspections=inspections,
                            test_runs=test_runs,
                            workflow_status="failed",
                            failure_reason=str(exc),
                        )
                        try:
                            audit_path = self.audits.write(
                                workspace=workspace,
                                task_id=task_id,
                                audit_result=audit_result,
                                source_artifacts=audit_source_artifacts(
                                    context_path=context_path,
                                    approval_paths=terminal_approval_paths,
                                    event_paths=event_paths,
                                    mutation_approval_paths=mutation_approval_paths,
                                    workflow_path=None,
                                ),
                            )
                        except Exception as audit_exc:
                            exc.add_note(f"failed audit write failed: {audit_exc}")
                        fix_proposals = (
                            build_fix_proposals(audit_result["findings"])
                            if audit_path is not None
                            else []
                        )
                        if fix_proposals and audit_path is not None:
                            try:
                                fix_path = self.fixes.write(
                                    workspace=workspace,
                                    task_id=task_id,
                                    proposals=fix_proposals,
                                    source_audit_artifact=str(audit_path),
                                )
                            except Exception as fix_exc:
                                exc.add_note(f"failed fix write failed: {fix_exc}")
                        node_results = developer_node_results(
                            agent_output=execution_agent_output,
                            test_runs=test_runs,
                            applied_changes=applied_changes,
                            denied_changes=denied_changes,
                            failure_reason=str(exc),
                        )
                        apply_audit_result(
                            node_results=node_results,
                            audit_result=audit_result,
                            fix_proposals=fix_proposals,
                            fix_path=fix_path,
                        )
                        optic_trace_path: Path | None = None
                        try:
                            optic_trace_path = self.workflows.optic_trace_path(task_id)
                            self.workflows.write_optic_trace(
                                workspace=workspace,
                                task_id=task_id,
                                trace=build_bug_fix_optic_trace(
                                    task_id=task_id,
                                    workspace=workspace,
                                    workflow_status="failed",
                                    node_results=node_results,
                                    context_path=context_path,
                                    approval_paths=terminal_approval_paths,
                                    event_paths=event_paths,
                                    audit_path=audit_path,
                                    fix_path=fix_path,
                                    optic_trace_path=optic_trace_path,
                                    applied_changes=applied_changes,
                                    mutation_approval_paths=mutation_approval_paths,
                                ),
                            )
                        except Exception as trace_exc:
                            optic_trace_path = None
                            exc.add_note(f"failed optic trace write failed: {trace_exc}")
                        workflow_kwargs = {
                            "audit_path": audit_path,
                            "fix_path": fix_path,
                            "optic_trace_path": optic_trace_path,
                            "node_results": node_results,
                            "audit_findings": audit_result["findings"],
                            "fix_proposals": fix_proposals,
                        }
                    self.workflows.write(
                        workspace=workspace,
                        task_id=task_id,
                        workflow=build_phase_one_workflow(
                            workspace=workspace,
                            execution_policy=execution_policy,
                            agent_output=execution_agent_output,
                            context_path=context_path,
                            approval_paths=terminal_approval_paths,
                            mutation_approval_paths=mutation_approval_paths,
                            event_paths=event_paths,
                            memory_paths=[],
                            inspections=inspections,
                            test_runs=test_runs,
                            applied_changes=applied_changes,
                            denied_changes=denied_changes,
                            status="failed",
                            **workflow_kwargs,
                        ),
                    )
            except Exception as workflow_exc:
                exc.add_note(f"failed workflow write failed: {workflow_exc}")
            raise

    def _record_agent_output_acceptance(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        agent_output: dict[str, Any],
    ) -> Path:
        return self.approvals.write_agent_output_acceptance(
            workspace=workspace,
            task_id=task_id,
            agent_output_id=agent_output["id"],
            bound_to_artifacts=[],
            approval_scope={
                "effects": agent_output.get("proposed_effects", []),
                "mutations": agent_output.get("proposed_changes", []),
            },
        )
