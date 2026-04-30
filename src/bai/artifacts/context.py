"""Scoped context bundle persistence for plan-agent runs.

Context artifacts capture only the request, workspace metadata, serial
execution policy, router constraints, and explicitly approved memory snippets.
They intentionally avoid retrieval, workspace crawling, and file contents.
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


class ContextStore:
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
        bundle: dict[str, Any],
    ) -> Path:
        path = self.runtime.state / "context" / f"{task_id}.json"
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "context_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        path.parent.mkdir(parents=True, exist_ok=True)
        context = {
            "task_id": task_id,
            "workspace_id": workspace.workspace_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "user_request": bundle["user_request"],
            "execution_policy": bundle["execution_policy"],
            "router_constraints": bundle["router_constraints"],
            "approved_memory_snippets": bundle.get("approved_memory_snippets", []),
            "workspace": {
                "workspace_id": workspace.workspace_id,
                "name": workspace.name,
                "root_path": workspace.root_path,
                "allowed_paths": workspace.allowed_paths,
                "memory_scope": workspace.memory_scope,
            },
        }
        atomic_write_text(path, json.dumps(context, indent=2, sort_keys=True) + "\n")
        return path
