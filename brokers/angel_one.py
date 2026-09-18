from __future__ import annotations
from typing import Any, Dict, List, Optional
from .base import BrokerAdapter

class AngelOneAdapter(BrokerAdapter):
    """Compatibility adapter for Angel One."""
    name = "angel_one"

    def __init__(self, backend_module=None, config=None):
        self.backend = backend_module
        self.config = config or {}

    def login(self) -> bool:
        return True

    def fetch_positions(self) -> List[Dict[str, Any]]:
        return []

    def fetch_watchlist(self) -> List[Dict[str, Any]]:
        return []

    def fetch_quotes(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return []