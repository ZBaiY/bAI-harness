from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bai.config.router import ProviderConfigError, ProviderConfigStore, Router
from bai.core.runtime import RuntimePaths


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def test_router_uses_local_stub_default_when_provider_config_is_missing(
    runtime: RuntimePaths,
) -> None:
    route = Router(runtime=runtime).select(
        provider_allowance={"local": True, "workspace_network": False}
    )

    assert route.provider == "local"
    assert route.model == "local-plan-stub"
    assert route.endpoint == "stub://local/plan"
    assert route.network_required is False
    assert not (runtime.config / "providers.json").exists()


def test_provider_config_store_writes_only_under_runtime_config(
    runtime: RuntimePaths, workspace_root: Path
) -> None:
    store = ProviderConfigStore(runtime)

    path = store.set_local(model="local-custom", endpoint="stub://local/custom")

    assert path == runtime.config / "providers.json"
    assert path.exists()
    assert path.is_relative_to(runtime.home)
    assert not path.is_relative_to(workspace_root)
    assert read_json(path) == {
        "providers": {
            "local": {
                "provider": "local",
                "model": "local-custom",
                "endpoint": "stub://local/custom",
                "network_required": False,
            }
        }
    }


def test_router_uses_local_config_override_when_network_is_not_required(
    runtime: RuntimePaths,
) -> None:
    ProviderConfigStore(runtime).set_local(
        model="local-custom",
        endpoint="stub://local/custom",
    )

    route = Router(runtime=runtime).select(
        provider_allowance={"local": True, "workspace_network": False}
    )

    assert route.provider == "local"
    assert route.model == "local-custom"
    assert route.endpoint == "stub://local/custom"
    assert route.network_required is False


def test_provider_config_rejects_network_endpoint_for_local_provider(
    runtime: RuntimePaths,
) -> None:
    with pytest.raises(ProviderConfigError, match="local endpoint must be local"):
        ProviderConfigStore(runtime).set_local(
            model="remote-ish",
            endpoint="https://example.com/model",
        )

    assert not (runtime.config / "providers.json").exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost.evil.com/model",
        "http://127.0.0.1.evil.com/model",
    ],
)
def test_provider_config_rejects_localhost_prefix_hostnames(
    runtime: RuntimePaths, endpoint: str
) -> None:
    with pytest.raises(ProviderConfigError, match="local endpoint must be local"):
        ProviderConfigStore(runtime).set_local(
            model="remote-disguised-as-local",
            endpoint=endpoint,
        )

    assert not (runtime.config / "providers.json").exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434/api",
        "http://127.0.0.1:11434/api",
    ],
)
def test_provider_config_allows_exact_local_http_hosts(
    runtime: RuntimePaths, endpoint: str
) -> None:
    path = ProviderConfigStore(runtime).set_local(
        model="local-http",
        endpoint=endpoint,
    )

    assert read_json(path)["providers"]["local"]["endpoint"] == endpoint


def test_provider_config_read_reports_malformed_json_as_config_error(
    runtime: RuntimePaths,
) -> None:
    path = runtime.config / "providers.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not-json")

    with pytest.raises(ProviderConfigError, match="provider config is invalid JSON"):
        ProviderConfigStore(runtime).read()


def test_router_denies_configured_cloud_provider_without_allowance(
    runtime: RuntimePaths,
) -> None:
    path = runtime.config / "providers.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "local": {
                        "provider": "local",
                        "model": "local-plan-stub",
                        "endpoint": "stub://local/plan",
                        "network_required": False,
                    },
                    "cloud": {
                        "provider": "cloud",
                        "model": "cloud-model",
                        "endpoint": "https://example.com/model",
                        "network_required": True,
                    },
                }
            }
        )
    )

    route = Router(runtime=runtime).select(
        provider_allowance={"local": True, "cloud": False, "workspace_network": False}
    )

    assert route.provider == "local"
    assert route.network_required is False


def test_router_rejects_local_route_when_local_provider_is_not_allowed(
    runtime: RuntimePaths,
) -> None:
    with pytest.raises(ProviderConfigError, match="local provider is not allowed"):
        Router(runtime=runtime).select(
            provider_allowance={"local": False, "workspace_network": False}
        )


def test_router_does_not_accept_prompt_or_workspace_context_arguments(
    runtime: RuntimePaths,
) -> None:
    router = Router(runtime=runtime)

    with pytest.raises(TypeError):
        router.select(  # type: ignore[call-arg]
            provider_allowance={"local": True},
            prompt="do not inspect",
        )
