"""Safe probes for gateway methods used by Hermes Plus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_plus.adapter.gateway import GatewayClient, RpcError
from hermes_plus.adapter.protocol import EVENT_NAMES, METHOD_NOT_FOUND_CODE

# Only idempotent read operations are probed. Their bounded parameters avoid
# creating sessions or changing configuration while confirming registration.
KNOWN_METHODS: dict[str, dict[str, dict[str, Any]]] = {
    "sessions": {"session.list": {"limit": 1}},
    "configuration": {"config.get": {"key": "theme"}},
    "profiles": {"profiles.list": {}},
}


@dataclass(frozen=True)
class Capabilities:
    version: str | None
    methods: set[str]
    events: set[str]
    features: dict[str, bool]


async def probe_capabilities(client: GatewayClient) -> Capabilities:
    """Probe known read-only methods and return methods available on this gateway."""
    available: set[str] = set()
    features: dict[str, bool] = {}
    for feature, methods in KNOWN_METHODS.items():
        feature_methods = []
        for name, params in methods.items():
            try:
                await client.call(name, params=params, timeout=15)
            except RpcError as exc:
                if exc.code == METHOD_NOT_FOUND_CODE:
                    continue
            available.add(name)
            feature_methods.append(name)
        features[feature] = len(feature_methods) == len(methods)
    version = getattr(client, "version", None)
    return Capabilities(
        version=version if isinstance(version, str) else None,
        methods=available,
        events=set(EVENT_NAMES),
        features=features,
    )
