"""Minimal session and working memory artifact store.

Phase one supports only session and working memory under BAI_HOME. There is no
long-term store, indexing, summarization, retrieval, or background memory
consolidation in this module.
"""

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


class MemoryStore:
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
        kind: str,
        content: dict[str, Any],
    ) -> Path:
        if kind not in {"session", "working"}:
            raise ValueError("phase-one memory supports only session and working writes")
        directory = self.runtime.memory / workspace.workspace_id / kind
        path = directory / f"{uuid.uuid4().hex}.json"
        decision = self.policy.gate_effect(
            workspace=workspace,
            effect={"kind": "memory_write", "path": str(path)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "workspace_id": workspace.workspace_id,
            "kind": kind,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "content": content,
        }
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path
