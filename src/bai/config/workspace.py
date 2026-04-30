from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.errors import WorkspaceConfigError
from ..core.io import atomic_write_text
from ..core.runtime import RuntimePaths


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_id: str
    name: str
    root_path: str
    allowed_paths: list[str]
    default_branch: str | None
    command_policy: dict[str, Any]
    network_policy: dict[str, Any]
    sandbox_path: str
    memory_scope: str
    sandbox_mode: str = "in_place_read"


def canonical_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def workspace_id_for(name: str, root_path: Path) -> str:
    digest = hashlib.sha256(f"{name}\0{root_path}".encode("utf-8")).hexdigest()
    return digest[:16]


def detect_default_branch(root_path: Path) -> str | None:
    if not (root_path / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root_path), "branch", "--show-current"],
            check=False,
            text=True,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = result.stdout.strip()
    return branch or None


class WorkspaceStore:
    def __init__(self, runtime: RuntimePaths | None = None) -> None:
        self.runtime = runtime or RuntimePaths.discover()
        self.directory = self.runtime.config / "workspaces"
        self.name_directory = self.runtime.config / "workspace_names"

    def ensure(self) -> None:
        self.runtime.ensure()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.name_directory.mkdir(parents=True, exist_ok=True)

    def add(self, name: str, root_path: str | Path) -> WorkspaceRecord:
        root = canonical_path(root_path)
        if not root.exists():
            raise WorkspaceConfigError(f"workspace root does not exist: {root}")
        if not root.is_dir():
            raise WorkspaceConfigError(f"workspace root must be a directory: {root}")
        self.ensure()
        workspace_id = workspace_id_for(name, root)
        sandbox_path = self.runtime.sandbox / workspace_id
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            name=name,
            root_path=str(root),
            allowed_paths=[str(root)],
            default_branch=detect_default_branch(root),
            command_policy={
                "inspect": {"allowed": True, "paths": [str(root)]},
                "test": {
                    "allowed": False,
                    "allowed_argv": [],
                    "allowed_prefixes": [],
                    "declared_writable_paths": [],
                    "write_enforcement": "trusted_command_no_sandbox",
                },
                "mutate": {"allowed": False},
                "workspace_network": {"allowed": False},
                "destructive": {"allowed": False},
            },
            network_policy={"workspace_network": False},
            sandbox_path=str(sandbox_path),
            memory_scope=f"workspace:{workspace_id}",
        )
        self._write(record)
        self._index_name(record)
        return record

    def list(self) -> list[WorkspaceRecord]:
        self.ensure()
        return [self._read(path) for path in sorted(self.directory.glob("*.json"))]

    def get(self, name_or_id: str) -> WorkspaceRecord:
        self.ensure()
        direct = self.directory / f"{name_or_id}.json"
        if direct.exists():
            return self._read(direct)
        index_path = self._name_index_path(name_or_id)
        if not index_path.exists():
            raise KeyError(
                f"workspace name index missing for {name_or_id}; "
                "use workspace id or re-add workspace"
            )
        data = json.loads(index_path.read_text())
        workspace_ids = [
            workspace_id
            for workspace_id in data.get("workspace_ids", [])
            if (self.directory / f"{workspace_id}.json").exists()
        ]
        if len(workspace_ids) == 1:
            return self._read(self.directory / f"{workspace_ids[0]}.json")
        if len(workspace_ids) > 1:
            raise KeyError(f"workspace name is ambiguous: {name_or_id}")
        raise KeyError(
            f"workspace name index is stale for {name_or_id}; "
            "use workspace id or re-add workspace"
        )

    def validate(self, record: WorkspaceRecord) -> WorkspaceRecord:
        root = canonical_path(record.root_path)
        allowed = [canonical_path(path) for path in record.allowed_paths]
        if not allowed:
            raise WorkspaceConfigError("workspace must have at least one allowed path")
        for path in allowed:
            if not (path == root or path.is_relative_to(root)):
                raise WorkspaceConfigError(f"allowed path escapes workspace root: {path}")
        sandbox = canonical_path(record.sandbox_path)
        self.runtime.assert_runtime_path(sandbox)
        return record

    def contains_allowed_path(self, record: WorkspaceRecord, path: str | Path) -> bool:
        self.validate(record)
        target = canonical_path(path)
        root = canonical_path(record.root_path)
        if not (target == root or target.is_relative_to(root)):
            return False
        for allowed in record.allowed_paths:
            allowed_path = canonical_path(allowed)
            if target == allowed_path or target.is_relative_to(allowed_path):
                return True
        return False

    def allow_test(
        self,
        name_or_id: str,
        *,
        argv: list[str] | None = None,
        argv_prefix: list[str] | None = None,
        writable_paths: list[str | Path] | None = None,
    ) -> WorkspaceRecord:
        if bool(argv) == bool(argv_prefix):
            raise WorkspaceConfigError("allow-test requires exactly one argv or argv prefix")
        if argv is not None and not _is_string_list(argv):
            raise WorkspaceConfigError("test argv must be a JSON array of strings")
        if argv_prefix is not None and not _is_string_list(argv_prefix):
            raise WorkspaceConfigError("test argv prefix must be a JSON array of strings")

        record = self.get(name_or_id)
        self.validate(record)
        writable = [
            self._canonical_test_writable_path(record, path)
            for path in (writable_paths or [])
        ]
        existing = record.command_policy.get("test", {})
        allowed_argv = [list(item) for item in existing.get("allowed_argv", [])]
        allowed_prefixes = [
            list(item) for item in existing.get("allowed_prefixes", [])
        ]
        existing_writable = [
            str(canonical_path(path))
            for path in existing.get(
                "declared_writable_paths", existing.get("writable_paths", [])
            )
        ]
        if argv is not None and argv not in allowed_argv:
            allowed_argv.append(argv)
        if argv_prefix is not None and argv_prefix not in allowed_prefixes:
            allowed_prefixes.append(argv_prefix)
        for path in writable:
            text = str(path)
            if text not in existing_writable:
                existing_writable.append(text)

        command_policy = {
            **record.command_policy,
            "test": {
                "allowed": True,
                "allowed_argv": allowed_argv,
                "allowed_prefixes": allowed_prefixes,
                "declared_writable_paths": existing_writable,
                "write_enforcement": "trusted_command_no_sandbox",
            },
        }
        updated = replace(record, command_policy=command_policy)
        self._write(updated)
        self._index_name(updated)
        return updated

    def _canonical_test_writable_path(
        self, record: WorkspaceRecord, path: str | Path
    ) -> Path:
        target = canonical_path(path)
        root = canonical_path(record.root_path)
        cache = canonical_path(self.runtime.cache)
        if target == root or target.is_relative_to(root):
            return target
        if target == cache or target.is_relative_to(cache):
            return target
        raise WorkspaceConfigError(
            f"writable path outside workspace or runtime cache: {target}"
        )

    def _write(self, record: WorkspaceRecord) -> None:
        path = self.directory / f"{record.workspace_id}.json"
        atomic_write_text(path, json.dumps(asdict(record), indent=2, sort_keys=True) + "\n")

    def _read(self, path: Path) -> WorkspaceRecord:
        data = json.loads(path.read_text())
        return WorkspaceRecord(**data)

    def _name_index_path(self, name: str) -> Path:
        return self.name_directory / f"{quote(name, safe='')}.json"

    def _index_name(self, record: WorkspaceRecord) -> None:
        path = self._name_index_path(record.name)
        if path.exists():
            data = json.loads(path.read_text())
            workspace_ids = list(data.get("workspace_ids", []))
        else:
            workspace_ids = []
        if record.workspace_id not in workspace_ids:
            workspace_ids.append(record.workspace_id)
        atomic_write_text(
            path,
            json.dumps(
                {"name": record.name, "workspace_ids": workspace_ids},
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )


def _is_string_list(value: list[str]) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and item for item in value
    )
