from __future__ import annotations

import shlex
import uuid
from dataclasses import dataclass
from typing import Any

from ..config.router import ModelRoute
from ..config.workspace import WorkspaceRecord
from ..core.errors import BaiUserError


@dataclass(frozen=True)
class PlanAgent:
    agent_kind: str = "plan"

    def run(
        self,
        *,
        request: str,
        workspace: WorkspaceRecord,
        execution_policy: str,
        model_route: ModelRoute,
    ) -> dict[str, Any]:
        proposed_changes: list[dict[str, Any]] = []
        proposed_effects: list[dict[str, Any]] = []
        workflow_mode = "dev" if request == "dev" or request.startswith("dev ") else "default"
        effective_request = request.removeprefix("dev").strip() if workflow_mode == "dev" else request
        command_request, test_request = _split_test_request(effective_request)
        if command_request.startswith("propose-create "):
            proposed_changes.append(_create_proposal(command_request.removeprefix("propose-create ").strip()))
        if command_request.startswith("inspect "):
            path = command_request.removeprefix("inspect ").strip()
            proposed_effects.append({"kind": "inspect_file", "path": path})
        if test_request is not None:
            proposed_effects.append(_test_effect(test_request))
        return {
            "id": f"plan-{uuid.uuid4().hex[:12]}",
            "agent_kind": self.agent_kind,
            "workflow_mode": workflow_mode,
            "workspace_id": workspace.workspace_id,
            "execution_policy": execution_policy,
            "model": {
                "provider": model_route.provider,
                "model": model_route.model,
                "endpoint": model_route.endpoint,
                "network_required": model_route.network_required,
            },
            "summary": f"Plan for: {request}",
            "ordered_steps": [
                "Validate workspace configuration",
                "Route to local plan model stub",
                "Prepare bounded plan output",
            ],
            "risks": [],
            "requested_approval_scope": proposed_changes,
            "proposed_changes": proposed_changes,
            "proposed_effects": proposed_effects,
        }


def _split_test_request(request: str) -> tuple[str, str | None]:
    if request == "test" or request.startswith("test "):
        return "", request
    marker = " test "
    if marker in request:
        command_request, raw_test = request.split(marker, 1)
        return command_request.strip(), f"test {raw_test.strip()}"
    return request, None


def _create_proposal(path: str) -> dict[str, Any]:
    return {
        "path": path,
        "operation": "create",
        "content": "# created by bai phase-one harness\n",
        "rationale": "bounded phase-one create proposal",
        "requires_approval": True,
    }


def _test_effect(request: str) -> dict[str, Any]:
    raw_argv = request.removeprefix("test").strip()
    try:
        argv = shlex.split(raw_argv) if raw_argv else []
    except ValueError as exc:
        raise BaiUserError(f"test command argv parse failed: {exc}") from exc
    return {"kind": "test_command", "argv": argv, "declared_writable_paths": []}
