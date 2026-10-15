"""The only Hermes-aware code, exposing the frozen v1 adapter contract."""

from hermes_plus.adapter.capabilities import Capabilities, probe_capabilities
from hermes_plus.adapter.cli import run_hermes
from hermes_plus.adapter.compat import Compat, check_compat, known_good_ref_commit
from hermes_plus.adapter.config import enable_plugin, load_config
from hermes_plus.adapter.dashboard import DashboardProcess
from hermes_plus.adapter.gateway import GatewayClient, RpcError
from hermes_plus.adapter.kanban import KanbanError, create_task, list_tasks, show_task
from hermes_plus.adapter.live_delivery import (
    deliver_to_live_owner,
    find_canonical_live_owner,
    notify_sender_status,
)
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
    "KanbanError",
    "McpClientManager",
    "RpcError",
    "check_compat",
    "create_task",
    "deliver_to_live_owner",
    "detect",
    "enable_plugin",
    "find_canonical_live_owner",
    "hermes_home",
    "known_good_ref_commit",
    "list_profiles",
    "list_tasks",
    "load_config",
    "notify_sender_status",
    "plus_home",
    "probe_capabilities",
    "run_hermes",
    "show_task",
]
