"""The only Hermes-aware code, exposing the frozen v1 adapter contract."""

from hermes_plus.adapter.capabilities import Capabilities, probe_capabilities
from hermes_plus.adapter.cli import run_hermes
from hermes_plus.adapter.compat import Compat, check_compat
from hermes_plus.adapter.config import enable_plugin, load_config
from hermes_plus.adapter.dashboard import DashboardProcess
from hermes_plus.adapter.gateway import GatewayClient, RpcError
from hermes_plus.adapter.mcp import McpClientManager
from hermes_plus.adapter.paths import hermes_home, list_profiles, plus_home
from hermes_plus.adapter.version import HermesInstall, detect

ADAPTER_API: int = 1

__all__ = [
    "ADAPTER_API",
    "Capabilities",
    "Compat",
    "DashboardProcess",
    "GatewayClient",
    "HermesInstall",
    "McpClientManager",
    "RpcError",
    "check_compat",
    "detect",
    "enable_plugin",
    "hermes_home",
    "list_profiles",
    "load_config",
    "plus_home",
    "probe_capabilities",
    "run_hermes",
]
