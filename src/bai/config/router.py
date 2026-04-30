"""Local-only provider configuration and deterministic route selection.

Router chooses a model route from BAI_HOME provider config or the built-in
local stub. It never inspects prompts, memory, or workspace files, and it does
not perform network health checks or cloud fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.errors import BaiUserError
from ..core.io import atomic_write_text
from ..core.runtime import RuntimePaths


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str
    endpoint: str
    network_required: bool = False


class ProviderConfigError(BaiUserError):
    """Provider configuration is missing, invalid, or not allowed."""


DEFAULT_PROVIDER_CONFIG = {
    "providers": {
        "local": {
            "provider": "local",
            "model": "local-plan-stub",
            "endpoint": "stub://local/plan",
            "network_required": False,
        }
    }
}


class ProviderConfigStore:
    def __init__(self, runtime: RuntimePaths | None = None) -> None:
        self.runtime = runtime or RuntimePaths.discover()
        self.path = self.runtime.config / "providers.json"

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return DEFAULT_PROVIDER_CONFIG
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError as exc:
            raise ProviderConfigError("provider config is invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
            raise ProviderConfigError("provider config must contain providers")
        return data

    def set_local(self, *, model: str, endpoint: str) -> Path:
        if not model:
            raise ProviderConfigError("local model is required")
        if not _is_local_endpoint(endpoint):
            raise ProviderConfigError("local endpoint must be local")
        self.runtime.ensure()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "providers": {
                "local": {
                    "provider": "local",
                    "model": model,
                    "endpoint": endpoint,
                    "network_required": False,
                }
            }
        }
        self.runtime.assert_runtime_path(self.path)
        atomic_write_text(self.path, json.dumps(config, indent=2, sort_keys=True) + "\n")
        return self.path


class Router:
    def __init__(
        self,
        *,
        runtime: RuntimePaths | None = None,
        config_store: ProviderConfigStore | None = None,
    ) -> None:
        self.runtime = runtime or RuntimePaths.discover()
        self.config_store = config_store or ProviderConfigStore(self.runtime)

    def select(self, *, provider_allowance: dict[str, bool] | None = None) -> ModelRoute:
        allowance = provider_allowance or {}
        if allowance.get("local", True) is not True:
            raise ProviderConfigError("local provider is not allowed")
        config = self.config_store.read()
        local = config.get("providers", {}).get("local", DEFAULT_PROVIDER_CONFIG["providers"]["local"])
        if local.get("provider") != "local":
            raise ProviderConfigError("local provider config is invalid")
        # Router is local-only in phase one; no network fallback or health check is attempted.
        # Provider allowance narrows what config may select; it never expands workspace network
        # policy or inspects task context.
        if local.get("network_required") is not False:
            raise ProviderConfigError("local provider must not require network")
        endpoint = str(local.get("endpoint", ""))
        if not _is_local_endpoint(endpoint):
            raise ProviderConfigError("local endpoint must be local")
        return ModelRoute(
            provider="local",
            model=str(local.get("model", "local-plan-stub")),
            endpoint=endpoint,
            network_required=False,
        )


def provider_config_to_json(config: dict[str, Any]) -> str:
    return json.dumps(config, indent=2, sort_keys=True)


def _is_local_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    if parsed.scheme in {"stub", "local", "file"}:
        return True
    # Hostname must parse exactly as local; prefix matches such as localhost.evil are denied.
    # These URLs may still use a local socket, so callers should treat them as local-only
    # endpoints rather than cloud/network provider access.
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
        return True
    return False
