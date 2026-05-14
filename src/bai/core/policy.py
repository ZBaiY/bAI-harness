"""Deterministic phase-one PolicyEngine gates for agent output and effects.

PolicyEngine is not an agent, an LLM, or a workflow step. It is the deterministic
non-bypassable boundary that validates agent output and every phase-one effect
immediately before the owning component writes, reads, runs, or mutates.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..config.workspace import WorkspaceRecord, WorkspaceStore


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


class PolicyEngineInterface(Protocol):
    def gate_agent_output(self, output: dict[str, Any]) -> PolicyDecision:
        ...

    def gate_effect(
        self,
        *,
        workspace: WorkspaceRecord,
        effect: dict[str, Any],
        approved: bool = False,
    ) -> PolicyDecision:
        ...


class PolicyEngine:
    """Deterministic phase-one policy gate."""

    def __init__(self, workspace_store: WorkspaceStore | None = None) -> None:
        self.workspace_store = workspace_store or WorkspaceStore()

    def gate_agent_output(self, output: dict[str, Any]) -> PolicyDecision:
        if output.get("agent_kind") != "plan":
            return PolicyDecision(False, "only stateless plan agent output is allowed")
        if output.get("execution_policy") != "serial":
            return PolicyDecision(False, "phase-one agent output must be serial")
        for change in output.get("proposed_changes", []):
            if change.get("operation") not in {"create", "modify"}:
                return PolicyDecision(False, "invalid proposed change operation")
            if not change.get("path"):
                return PolicyDecision(False, "proposed change requires a path")
        for effect in output.get("proposed_effects", []):
            if effect.get("kind") == "inspect_file":
                if not effect.get("path"):
                    return PolicyDecision(False, "inspect effect requires a path")
                continue
            if effect.get("kind") == "test_command":
                argv = effect.get("argv")
                if not _is_string_list(argv):
                    return PolicyDecision(False, "test command requires argv")
                continue
            else:
                return PolicyDecision(False, "invalid proposed effect kind")
        return PolicyDecision(True, "agent output accepted")

    def gate_effect(
        self,
        *,
        workspace: WorkspaceRecord,
        effect: dict[str, Any],
        approved: bool = False,
    ) -> PolicyDecision:
        kind = effect.get("kind")
        # Runtime artifact effects are constrained to their owned BAI_HOME subtrees.
        # This keeps approvals, memory, task events, workflows, audits, fixes, and scheduler
        # records from being redirected into workspaces or the source checkout.
        if kind == "approval_write":
            target = Path(effect["path"])
            try:
                resolved = self.workspace_store.runtime.assert_runtime_path(target)
            except ValueError as exc:
                return PolicyDecision(False, str(exc))
            approval_dir = self.workspace_store.runtime.state / "approvals"
            if not resolved.is_relative_to(approval_dir):
                return PolicyDecision(False, "approval writes must stay under runtime approvals")
            return PolicyDecision(True, "approval write accepted")
        if kind == "memory_write":
            target = Path(effect["path"])
            try:
                resolved = self.workspace_store.runtime.assert_runtime_path(target)
            except ValueError as exc:
                return PolicyDecision(False, str(exc))
            if not resolved.is_relative_to(self.workspace_store.runtime.memory):
                return PolicyDecision(False, "memory writes must stay under runtime memory")
            return PolicyDecision(True, "memory write accepted")
        if kind == "task_event_write":
            target = Path(effect["path"])
            try:
                resolved = self.workspace_store.runtime.assert_runtime_path(target)
            except ValueError as exc:
                return PolicyDecision(False, str(exc))
            event_dir = self.workspace_store.runtime.state / "events"
            if not resolved.is_relative_to(event_dir):
                return PolicyDecision(False, "task event writes must stay under runtime events")
            return PolicyDecision(True, "task event write accepted")
        if kind == "workflow_write":
            target = Path(effect["path"])
            try:
                resolved = self.workspace_store.runtime.assert_runtime_path(target)
            except ValueError as exc:
                return PolicyDecision(False, str(exc))
            workflow_dir = self.workspace_store.runtime.state / "workflows"
            if not resolved.is_relative_to(workflow_dir):
                return PolicyDecision(False, "workflow writes must stay under runtime workflows")
            return PolicyDecision(True, "workflow write accepted")
        if kind == "context_write":
            target = Path(effect["path"])
            try:
                resolved = self.workspace_store.runtime.assert_runtime_path(target)
            except ValueError as exc:
                return PolicyDecision(False, str(exc))
            context_dir = self.workspace_store.runtime.state / "context"
            if not resolved.is_relative_to(context_dir):
                return PolicyDecision(False, "context writes must stay under runtime context")
            return PolicyDecision(True, "context write accepted")
        if kind == "audit_write":
            target = Path(effect["path"])
            try:
                resolved = self.workspace_store.runtime.assert_runtime_path(target)
            except ValueError as exc:
                return PolicyDecision(False, str(exc))
            audit_dir = self.workspace_store.runtime.state / "audits"
            if not resolved.is_relative_to(audit_dir):
                return PolicyDecision(False, "audit writes must stay under runtime audits")
            return PolicyDecision(True, "audit write accepted")
        if kind == "fix_write":
            target = Path(effect["path"])
            try:
                resolved = self.workspace_store.runtime.assert_runtime_path(target)
            except ValueError as exc:
                return PolicyDecision(False, str(exc))
            fix_dir = self.workspace_store.runtime.state / "fixes"
            if not resolved.is_relative_to(fix_dir):
                return PolicyDecision(False, "fix writes must stay under runtime fixes")
            return PolicyDecision(True, "fix write accepted")
        if kind == "scheduler_write":
            target = Path(effect["path"])
            try:
                resolved = self.workspace_store.runtime.assert_runtime_path(target)
            except ValueError as exc:
                return PolicyDecision(False, str(exc))
            scheduler_dir = self.workspace_store.runtime.state / "scheduler"
            if not resolved.is_relative_to(scheduler_dir):
                return PolicyDecision(False, "scheduler writes must stay under runtime scheduler state")
            return PolicyDecision(True, "scheduler write accepted")
        if kind == "inspect_file":
            inspect_policy = workspace.command_policy.get("inspect", {})
            if inspect_policy.get("allowed") is not True:
                return PolicyDecision(False, "inspect command policy denies inspection")
            target = Path(effect["path"])
            if not self.workspace_store.contains_allowed_path(workspace, target):
                return PolicyDecision(False, "inspect target outside allowed workspace paths")
            return PolicyDecision(True, "inspect accepted")
        if kind == "test_command":
            return self._gate_test_command(workspace=workspace, effect=effect)
        if kind == "code_mutation":
            # Mutation effects require both explicit approval and workspace containment.
            # The effect gate does not write; it establishes that the later mutation adapter
            # may inspect preimage and attempt the approved operation.
            if not approved:
                return PolicyDecision(False, "mutation requires harness approval")
            if effect.get("operation") not in {"create", "modify"}:
                return PolicyDecision(False, "phase one allows only create/modify mutation")
            target = Path(effect["path"])
            if not self.workspace_store.contains_allowed_path(workspace, target):
                return PolicyDecision(False, "mutation target outside allowed workspace paths")
            return PolicyDecision(True, "approved mutation accepted")
        return PolicyDecision(False, f"unknown effect kind: {kind}")

    def _gate_test_command(
        self, *, workspace: WorkspaceRecord, effect: dict[str, Any]
    ) -> PolicyDecision:
        test_policy = workspace.command_policy.get("test", {})
        if test_policy.get("allowed") is not True:
            return PolicyDecision(False, "test command policy denies execution")
        argv = effect.get("argv")
        if not _is_string_list(argv):
            return PolicyDecision(False, "test argv must be a JSON array of strings")
        cwd = Path(str(effect.get("cwd", ""))).expanduser().resolve()
        root = Path(workspace.root_path).expanduser().resolve()
        if cwd != root:
            return PolicyDecision(False, "test command cwd must be the workspace root")
        allowed_argv = test_policy.get("allowed_argv", [])
        allowed_prefixes = test_policy.get("allowed_prefixes", [])
        if argv not in allowed_argv and not _matches_prefix(argv, allowed_prefixes):
            return PolicyDecision(False, "test command argv not approved")
        approved_writable = [
            Path(path).expanduser().resolve()
            for path in test_policy.get(
                "declared_writable_paths", test_policy.get("writable_paths", [])
            )
        ]
        writable_paths = effect.get(
            "declared_writable_paths", effect.get("writable_paths", [])
        )
        if not isinstance(writable_paths, list) or not all(
            isinstance(path, str) for path in writable_paths
        ):
            return PolicyDecision(False, "test writable paths must be a JSON array")
        for raw_path in writable_paths:
            # This checks declaration scope only; execution remains trusted direct execution.
            # The subprocess adapter cannot enforce filesystem confinement in phase one, so
            # command approval is treated as trust in the exact argv/prefix.
            target = Path(raw_path).expanduser().resolve()
            if not any(
                target == approved or target.is_relative_to(approved)
                for approved in approved_writable
            ):
                return PolicyDecision(False, "test writable path is not approved")
        return PolicyDecision(True, "test command accepted")


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and item for item in value
    )


def _matches_prefix(argv: list[str], prefixes: Any) -> bool:
    if not isinstance(prefixes, list):
        return False
    for prefix in prefixes:
        if _is_string_list(prefix) and len(argv) >= len(prefix) and argv[: len(prefix)] == prefix:
            return True
    return False
