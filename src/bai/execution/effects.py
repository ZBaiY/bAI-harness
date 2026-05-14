"""Policy-gated execution adapters for inspect, test, and mutation effects.

EffectExecutor owns the actual filesystem/subprocess mutation boundary. Every
effect is checked by PolicyEngine before the corresponding read, command, or
write, and mutations also require approval and preimage binding.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - platform fallback
    fcntl = None  # type: ignore[assignment]

from ..artifacts.approvals import ApprovalStore
from ..config.workspace import WorkspaceRecord, WorkspaceStore
from ..core.errors import BaiUserError, HarnessBoundaryError
from ..core.io import fsync_directory, write_temp_text
from ..core.policy import PolicyEngineInterface
from ..core.runtime import RuntimePaths
from .test_command import TestCommandExecutor

INSPECTION_PREVIEW_CHARS = 4096


class TestCommandRunError(BaiUserError):
    """Raised after recording a nonzero trusted test command result."""

    def __init__(self, message: str, test_runs: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.test_runs = list(test_runs)


class EffectExecutor:
    def __init__(
        self,
        *,
        runtime: RuntimePaths,
        workspaces: WorkspaceStore,
        policy: PolicyEngineInterface,
        approvals: ApprovalStore,
        test_executor: TestCommandExecutor | None = None,
    ) -> None:
        self.runtime = runtime
        self.workspaces = workspaces
        self.policy = policy
        self.approvals = approvals
        self.test_executor = test_executor or TestCommandExecutor()

    def execute_inspections(
        self,
        *,
        workspace: WorkspaceRecord,
        proposed_effects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        inspections: list[dict[str, Any]] = []
        for effect in proposed_effects:
            if effect.get("kind") != "inspect_file":
                continue
            target = self._resolve_workspace_path(workspace, effect["path"])
            # Inspection is read-only, but still gated before any filesystem touch. This
            # prevents path escapes, missing policy state, or disabled inspect policy from
            # becoming observable through exists()/is_file()/open() calls.
            decision = self.policy.gate_effect(
                workspace=workspace,
                effect={"kind": "inspect_file", "path": str(target)},
            )
            if not decision.allowed:
                raise PermissionError(decision.reason)
            if not target.exists():
                raise FileNotFoundError(f"inspect target does not exist: {target}")
            if not target.is_file():
                raise HarnessBoundaryError(f"inspect target is not a file: {target}")
            with target.open("r", encoding="utf-8", errors="replace") as handle:
                preview = handle.read(INSPECTION_PREVIEW_CHARS)
                sentinel = handle.read(1)
            inspections.append(
                {
                    "path": str(target),
                    "status": "success",
                    "content_preview": preview,
                    "truncated": bool(sentinel),
                }
            )
        return inspections

    def execute_tests(
        self,
        *,
        workspace: WorkspaceRecord,
        proposed_effects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        test_runs: list[dict[str, Any]] = []
        for effect in proposed_effects:
            if effect.get("kind") != "test_command":
                continue
            argv = list(effect["argv"])
            cwd = Path(workspace.root_path).expanduser().resolve()
            # Writable paths are declared policy metadata, not an OS-level sandbox. The
            # approved argv is therefore trusted direct execution, and the result records
            # trusted_command_no_sandbox so callers do not infer confinement.
            declared_writable_paths = [
                str(Path(path).expanduser().resolve())
                for path in effect.get(
                    "declared_writable_paths", effect.get("writable_paths", [])
                )
            ]
            decision = self.policy.gate_effect(
                workspace=workspace,
                effect={
                    "kind": "test_command",
                    "argv": argv,
                    "cwd": str(cwd),
                    "declared_writable_paths": declared_writable_paths,
                },
            )
            if not decision.allowed:
                raise PermissionError(decision.reason)
            result = self.test_executor.run(
                argv=argv,
                cwd=cwd,
                declared_writable_paths=declared_writable_paths,
            )
            test_runs.append(result)
            if result["exit_code"] != 0:
                # Nonzero tests fail the run so success memory is not written. Failed-event
                # recovery happens in Harness, where task lifecycle context is available.
                raise TestCommandRunError(
                    f"test command failed with exit code {result['exit_code']}",
                    test_runs,
                )
        return test_runs

    def execute_approved_mutations(
        self,
        *,
        workspace: WorkspaceRecord,
        task_id: str,
        agent_output_id: str,
        proposed_changes: list[dict[str, Any]],
        approved_mutation_paths: list[str],
        bound_to_artifacts: list[str],
    ) -> tuple[list[dict[str, str]], list[Path], list[dict[str, str]]]:
        approved = {
            str(Path(path).expanduser().resolve()) for path in approved_mutation_paths
        }
        applied: list[dict[str, str]] = []
        mutation_approval_paths: list[Path] = []
        denied: list[dict[str, str]] = []
        for change in proposed_changes:
            target = Path(change["path"]).expanduser().resolve()
            operation = str(change["operation"])
            content = str(change.get("content", ""))
            content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
            # Policy must approve the exact target before preimage reads or mutation locks.
            # Even approved CLI paths are rechecked here so out-of-scope files are not probed.
            decision = self.policy.gate_effect(
                workspace=workspace,
                effect={
                    "kind": "code_mutation",
                    "path": str(target),
                    "operation": operation,
                },
                approved=str(target) in approved,
            )
            if not decision.allowed:
                if str(target) in approved:
                    raise PermissionError(decision.reason)
                denied.append(
                    {
                        "path": str(target),
                        "operation": operation,
                        "status": "denied",
                        "reason": decision.reason,
                    }
                )
                continue
            with self.mutation_guard(target):
                # Approval is bound to the current preimage immediately before the write.
                # The later preimage check catches changes between approval artifact creation
                # and atomic replacement/link.
                expected_preimage = self.file_preimage(target)
                self._validate_mutation_preimage(
                    operation=operation,
                    target=target,
                    preimage=expected_preimage,
                )
                mutation_approval_paths.append(
                    self.approvals.write_mutation_approval(
                        workspace=workspace,
                        task_id=task_id,
                        agent_output_id=agent_output_id,
                        approved_path=str(target),
                        operation=operation,
                        approving_mechanism="cli_flag",
                        bound_to_artifacts=bound_to_artifacts,
                        content_sha256=content_sha256,
                        expected_preimage=expected_preimage,
                    )
                )
                self._write_mutation_content(
                    operation=operation,
                    target=target,
                    content=content,
                    expected_preimage=expected_preimage,
                )
            applied.append({"path": str(target), "operation": operation})
        return applied, mutation_approval_paths, denied

    @contextlib.contextmanager
    def mutation_guard(self, target: Path):
        lock_dir = self.runtime.assert_runtime_path(
            self.runtime.state / "mutation_locks"
        )
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(str(target).encode("utf-8")).hexdigest() + ".lock"
        lock_path = lock_dir / lock_name
        if fcntl is None:
            # Portable fallback: exclusive directory creation represents the lock. It is
            # intentionally local and stale locks are surfaced to the user, not auto-expired.
            directory_lock = lock_dir / f"{lock_name}.d"
            try:
                directory_lock.mkdir()
            except FileExistsError as exc:
                raise PermissionError(
                    f"mutation target is locked: {target}; lock path: {directory_lock}"
                ) from exc
            try:
                yield
            finally:
                directory_lock.rmdir()
            return
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def file_preimage(self, target: Path) -> dict[str, str | bool | None]:
        if not target.exists():
            return {"exists": False, "sha256": None}
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "exists": True,
            "sha256": digest.hexdigest(),
        }

    def _resolve_workspace_path(
        self, workspace: WorkspaceRecord, path: str | Path
    ) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = Path(workspace.root_path) / candidate
        return candidate.resolve()

    def _write_mutation_content(
        self,
        *,
        operation: str,
        target: Path,
        content: str,
        expected_preimage: dict[str, str | bool | None],
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = write_temp_text(target.parent, target.name, content)
        try:
            # Recheck preimage inside the guarded write boundary to catch races. If another
            # process changed the target after approval, this mutation no longer matches its
            # approval scope and must fail.
            if self.file_preimage(target) != expected_preimage:
                raise PermissionError("mutation target changed after approval")
            if operation == "create":
                try:
                    os.link(temp_path, target)
                    fsync_directory(target.parent)
                except FileExistsError as exc:
                    raise PermissionError(f"create target already exists: {target}") from exc
            elif operation == "modify":
                os.replace(temp_path, target)
                fsync_directory(target.parent)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def _validate_mutation_preimage(
        self,
        *,
        operation: str,
        target: Path,
        preimage: dict[str, str | bool | None],
    ) -> None:
        if operation == "create" and preimage["exists"] is True:
            raise PermissionError(f"create target already exists: {target}")
        if operation == "modify" and preimage["exists"] is False:
            raise PermissionError(f"modify target does not exist: {target}")
