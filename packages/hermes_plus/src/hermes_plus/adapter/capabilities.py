"""Safe probes for gateway methods used by Hermes Plus."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from hermes_plus.adapter.gateway import GatewayClient, RpcError
from hermes_plus.adapter.protocol import EVENT_NAMES, METHOD_NOT_FOUND_CODE

log = logging.getLogger(__name__)

# Only idempotent read operations are probed. Their bounded parameters avoid
# creating sessions or changing configuration while confirming registration.
KNOWN_METHODS: dict[str, dict[str, dict[str, Any]]] = {
    "sessions": {"session.list": {"limit": 1}},
    "configuration": {"config.get": {"key": "theme"}},
    "profiles": {"profiles.list": {}},
    "bot_relay": {"bot_relay.roster.sync": {"agents": []}},
}

# A just-started upstream on a busy host can miss the first deadline while it
# warms up, so each probe is retried before the method is called absent: `owns()`
# forwards on that answer, and a wrong "absent" would make us answer upstream
# methods ourselves.
PROBE_TIMEOUT = 20.0
PROBE_ATTEMPTS = 3


@dataclass(frozen=True)
class Capabilities:
    version: str | None
    methods: set[str]
    events: set[str]
    features: dict[str, bool]
    # False only for the "we could not ask the gateway" fallback: with
    # known=False, shared-namespace methods must forward upstream because a
    # local handler there would shadow a stock method we never saw (see
    # rpc/registry.owns).
    known: bool = True


async def _probe_method(client: GatewayClient, name: str, params: dict[str, Any]) -> bool:
    """True when the gateway owns the method; False when it reports method-not-found."""
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        try:
            await client.call(name, params=params, timeout=PROBE_TIMEOUT)
            return True
        except RpcError as exc:
            return exc.code != METHOD_NOT_FOUND_CODE
        except (asyncio.TimeoutError, TimeoutError):
            log.warning(
                "capability probe %s timed out (attempt %d/%d)", name, attempt, PROBE_ATTEMPTS
            )
    raise RuntimeError(
        f"the upstream gateway never answered {name} in {PROBE_ATTEMPTS} probes of "
        f"{PROBE_TIMEOUT:g}s; refusing to guess whether Hermes owns it"
    )


async def probe_capabilities(client: GatewayClient) -> Capabilities:
    """Probe known read-only methods and return methods available on this gateway."""
    available: set[str] = set()
    features: dict[str, bool] = {}
    for feature, methods in KNOWN_METHODS.items():
        feature_methods = []
        for name, params in methods.items():
            if await _probe_method(client, name, params):
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
