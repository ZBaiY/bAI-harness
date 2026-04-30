"""Structured task lifecycle event artifact store.

Task events are append-style JSON lifecycle markers such as started, completed,
and failed. They provide completion evidence independently from approval
records, which only prove acceptance of a checkpoint or scope.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..config.workspace import WorkspaceRecord
from ..core.io import atomic_write_text
from ..core.policy import PolicyEngine
from ..core.runtime import RuntimePaths


class TaskEventStore:
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
        event_type: str,
        reason: str,
        checkpoint_ref: str | None = None,
    ) -> Path:
        created_at = datetime.now(timezone.utc).isoformat()
        filename = f"{created_at.replace(':', '').replace('+', 'Z')}-{event_type}-{uuid.uuid4().hex}.json"
        path = self.runtime.state / "events" / task_id / filename
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "task_event_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "task_id": task_id,
            "event_type": event_type,
            "workspace_id": workspace.workspace_id,
            "created_at": created_at,
            "reason": reason,
            "checkpoint_ref": checkpoint_ref,
        }
        atomic_write_text(path, json.dumps(event, indent=2, sort_keys=True) + "\n")
        return path
