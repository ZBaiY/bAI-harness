from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

from ..artifacts.approvals import ApprovalStore
from ..artifacts.context import ContextStore
from ..artifacts.memory import MemoryStore
from ..artifacts.task_events import TaskEventStore
from ..artifacts.workflow import (
    WorkflowStore,
    build_phase_one_workflow,
    developer_node_results,
)
from ..config.router import Router
from ..config.workspace import WorkspaceRecord, WorkspaceStore
from ..core.errors import BaiUserError, HarnessBoundaryError
from ..core.policy import PolicyEngine, PolicyEngineInterface
from ..core.runtime import RuntimePaths
from .agent import PlanAgent
from .effects import EffectExecutor, INSPECTION_PREVIEW_CHARS
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
        if execution_policy != "serial":
            raise ValueError("phase one defaults to serial and accepts only serial execution")
        workspace = self.workspaces.get(workspace_ref)
        self.workspaces.validate(workspace)
        try:
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
            policy_agent_output = copy.deepcopy(agent_output)
            execution_agent_output = copy.deepcopy(agent_output)
            workflow_mode = execution_agent_output.get("workflow_mode", "default")
            output_decision = self.policy.gate_agent_output(policy_agent_output)
            if not output_decision.allowed:
                raise PermissionError(output_decision.reason)
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
            audit_findings = node_results["audit"]["findings"]
            fix_proposals = node_results["fix"]["proposals"]
            memory_path = self.memory.write(
                workspace=workspace,
                kind="session",
                content={"request": request, "plan_id": execution_agent_output["id"]},
            )
            working_memory_path = self.memory.write(
                workspace=workspace,
                kind="working",
                content={
                    "plan_id": execution_agent_output["id"],
                    "task_id": task_id,
                    "workspace_id": workspace.workspace_id,
                    "workflow_mode": workflow_mode,
                    "risks": execution_agent_output.get("risks", []),
                    "requested_approval_scope": execution_agent_output.get(
                        "requested_approval_scope", []
                    ),
                    "workflow_artifact": str(workflow_path),
                    "approval_artifacts": [str(path) for path in approval_paths],
                },
            )
            memory_paths = [memory_path, working_memory_path]
            workflow_status = "completed_with_denials" if denied_changes else "completed"
            event_paths.append(
                self.events.write(
                    workspace=workspace,
                    task_id=task_id,
                    event_type="completed",
                    reason="run completed",
                    checkpoint_ref=str(approval_path),
                )
            )
            workflow_path = self.workflows.write(
                workspace=workspace,
                task_id=task_id,
                workflow=build_phase_one_workflow(
                    workspace=workspace,
                    execution_policy=execution_policy,
                    agent_output=execution_agent_output,
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
                    node_results=node_results,
                    audit_findings=audit_findings,
                    fix_proposals=fix_proposals,
                ),
            )
            return {
                "status": "completed_with_denials" if denied_changes else "success",
                "task_id": task_id,
                "execution_policy": execution_policy,
                "workspace_id": workspace.workspace_id,
                "workflow_mode": workflow_mode,
                "model": execution_agent_output["model"],
                "plan": execution_agent_output,
                "workflow_artifact": str(workflow_path),
                "context_artifact": str(context_path),
                "approval_artifact": str(approval_path),
                "approval_artifacts": [str(path) for path in approval_paths],
                "memory_artifact": str(memory_path),
                "working_memory_artifact": str(working_memory_path),
                "memory_artifacts": [str(path) for path in memory_paths],
                "event_artifacts": [str(path) for path in event_paths],
                "applied_changes": applied_changes,
                "denied_changes": denied_changes,
                "inspections": inspections,
                "test_runs": test_runs,
                "node_results": node_results,
                "audit_findings": audit_findings,
                "fix_proposals": fix_proposals,
            }
        except EXPECTED_RUN_FAILURES as exc:
            try:
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
                        node_results = developer_node_results(
                            agent_output=execution_agent_output,
                            test_runs=test_runs,
                            applied_changes=applied_changes,
                            denied_changes=denied_changes,
                            failure_reason=str(exc),
                        )
                        workflow_kwargs = {
                            "node_results": node_results,
                            "audit_findings": [],
                            "fix_proposals": [],
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
