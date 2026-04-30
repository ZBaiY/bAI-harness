from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config.workspace import WorkspaceRecord, WorkspaceStore
from ..core.io import atomic_write_text
from ..core.policy import PolicyEngine, PolicyEngineInterface
from ..core.runtime import RuntimePaths


class Scheduler:
    foreground_priority = "foreground_over_background"

    def __init__(
        self,
        *,
        runtime: RuntimePaths | None = None,
        workspaces: WorkspaceStore | None = None,
        policy: PolicyEngineInterface | None = None,
    ) -> None:
        self.runtime = runtime or RuntimePaths.discover()
        self.workspaces = workspaces or WorkspaceStore(self.runtime)
        self.policy = policy or PolicyEngine(self.workspaces)
        self.directory = self.runtime.state / "scheduler"

    def defer_background(
        self,
        *,
        workspace: WorkspaceRecord,
        request: str,
        execution_policy: str,
        reason: str,
    ) -> Path:
        self._require_serial(execution_policy)
        task_id = f"scheduler-{_task_token()}"
        record = {
            "task_id": task_id,
            "workspace_id": workspace.workspace_id,
            "request": request,
            "priority": "background",
            "lifecycle_status": "deferred",
            "preemptible": True,
            "created_at": _now(),
            "reason": reason,
            "execution_policy": execution_policy,
            "execution_state": "deferred_metadata_only",
            "preempts_background": False,
            "preempted_background_task_ids": [],
        }
        return self._write(workspace=workspace, record=record)

    def admit_foreground(
        self,
        *,
        workspace: WorkspaceRecord,
        request: str,
        execution_policy: str,
        reason: str,
    ) -> Path:
        self._require_serial(execution_policy)
        preempted = self._mark_background_preemptible(workspace)
        task_id = f"scheduler-{_task_token()}"
        record = {
            "task_id": task_id,
            "workspace_id": workspace.workspace_id,
            "request": request,
            "priority": "foreground",
            "lifecycle_status": "accepted",
            "preemptible": False,
            "created_at": _now(),
            "reason": reason,
            "execution_policy": execution_policy,
            "execution_state": "accepted_for_foreground_run",
            "preempts_background": bool(preempted),
            "preempted_background_task_ids": preempted,
        }
        return self._write(workspace=workspace, record=record)

    def complete_foreground(
        self,
        *,
        workspace: WorkspaceRecord,
        scheduler_path: Path,
        harness_task_id: str,
        status: str,
    ) -> Path:
        record = json.loads(scheduler_path.read_text())
        self._validate_foreground_record(workspace=workspace, record=record)
        record["lifecycle_status"] = "completed"
        record["execution_state"] = "foreground_run_completed"
        record["harness_task_id"] = harness_task_id
        record["run_status"] = status
        record["completed_at"] = _now()
        return self._write(workspace=workspace, record=record)

    def fail_foreground(
        self,
        *,
        workspace: WorkspaceRecord,
        scheduler_path: Path,
        failure_reason: str,
    ) -> Path:
        record = json.loads(scheduler_path.read_text())
        self._validate_foreground_record(workspace=workspace, record=record)
        record["lifecycle_status"] = "failed"
        record["execution_state"] = "foreground_run_failed"
        record["failure_reason"] = failure_reason
        record["failed_at"] = _now()
        return self._write(workspace=workspace, record=record)

    def _mark_background_preemptible(self, workspace: WorkspaceRecord) -> list[str]:
        preempted: list[str] = []
        for path in sorted(self.directory.glob("*.json")):
            record = json.loads(path.read_text())
            if record.get("workspace_id") != workspace.workspace_id:
                continue
            if record.get("priority") != "background":
                continue
            if record.get("execution_state") != "deferred_metadata_only":
                continue
            if record.get("lifecycle_status") not in {"deferred", "preemptible_deferred"}:
                continue
            record["lifecycle_status"] = "preemptible_deferred"
            record["preemptible"] = True
            record["reason"] = "foreground task has priority over planned background work"
            self._write(workspace=workspace, record=record)
            preempted.append(str(record["task_id"]))
        return preempted

    def _write(self, *, workspace: WorkspaceRecord, record: dict[str, Any]) -> Path:
        path = self.directory / f"{record['task_id']}.json"
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "scheduler_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
        return path

    @staticmethod
    def _require_serial(execution_policy: str) -> None:
        if execution_policy != "serial":
            raise ValueError("scheduler accepts only serial execution")

    @staticmethod
    def _validate_foreground_record(
        *, workspace: WorkspaceRecord, record: dict[str, Any]
    ) -> None:
        if record.get("workspace_id") != workspace.workspace_id:
            raise ValueError("scheduler record workspace mismatch")
        if record.get("priority") != "foreground":
            raise ValueError("scheduler record is not foreground admission")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_token() -> str:
    return uuid.uuid4().hex[:12]
