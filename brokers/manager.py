from __future__ import annotations

from .angel_one import AngelOneAdapter
from .config import (
    DEFAULT_BROKER,
    get_active_broker_name,
    get_broker_secrets,
    get_supported_brokers as config_supported_brokers,
)
from .indmoney import IndMoneyAdapter

# Optional import for US stocks adapter
try:
    from .us_stocks import USStocksAdapter
except ImportError:
    USStocksAdapter = None


def get_active_broker_adapter(backend_module=None):
    """
    Factory function resolving the active broker adapter based on runtime config.
    """
    broker_name = get_active_broker_name()

    if broker_name == "indmoney":
        return IndMoneyAdapter(config=get_broker_secrets("indmoney"))

    if broker_name == "us_stocks" and USStocksAdapter is not None:
        return USStocksAdapter()

    return AngelOneAdapter(backend_module=backend_module, config=get_broker_secrets("angel_one"))


def get_supported_brokers():
    return list(config_supported_brokers())


__all__ = ["get_active_broker_adapter", "get_supported_brokers", "DEFAULT_BROKER"]