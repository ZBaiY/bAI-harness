from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    config: Path
    state: Path
    logs: Path
    memory: Path
    cache: Path
    sandbox: Path

    @classmethod
    def discover(cls) -> "RuntimePaths":
        home = Path(os.environ.get("BAI_HOME", "~/.bai")).expanduser().resolve()
        return cls(
            home=home,
            config=home / "config",
            state=home / "state",
            logs=home / "logs",
            memory=home / "memory",
            cache=home / "cache",
            sandbox=home / "sandbox",
        )

    def ensure(self) -> None:
        for path in (
            self.config,
            self.state,
            self.logs,
            self.memory,
            self.cache,
            self.sandbox,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def assert_runtime_path(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if not (resolved == self.home or resolved.is_relative_to(self.home)):
            raise ValueError(f"path must stay under runtime home: {resolved}")
        return resolved
