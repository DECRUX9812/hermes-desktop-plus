"""Newline-delimited JSON-RPC 2.0 surface over plus.db."""

import hermes_plus.rpc.handlers  # noqa: F401  registers every method
from hermes_plus.rpc.context import RpcContext, UpstreamCaller, UpstreamRpcError, require_upstream
from hermes_plus.rpc.errors import RpcFault
from hermes_plus.rpc.registry import handle_text, methods

__all__ = [
    "RpcContext",
    "RpcFault",
    "UpstreamCaller",
    "UpstreamRpcError",
    "handle_text",
    "methods",
    "require_upstream",
]
