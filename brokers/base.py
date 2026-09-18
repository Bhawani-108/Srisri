from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BrokerAdapter(ABC):
    """Common contract for broker integrations."""

    name: str = "base"

    @abstractmethod
    def login(self) -> bool:
        """Authenticate with the broker and return True if successful."""
        raise NotImplementedError

    @abstractmethod
    def fetch_positions(self) -> List[Dict[str, Any]]:
        """Return normalized holding rows for the dashboard."""
        raise NotImplementedError

    @abstractmethod
    def fetch_watchlist(self) -> List[Dict[str, Any]]:
        """Return normalized watchlist rows for the dashboard."""
        raise NotImplementedError

    @abstractmethod
    def fetch_quotes(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Return normalized live quote rows with CMP, PC, Day High, and Volume."""
        raise NotImplementedError

    def normalize_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Enforce the internal schema used by the dashboard."""
        normalized = {
            "Stock Name": row.get("Stock Name") or row.get("Symbol") or row.get("Ticker") or "",
            "Exchange": row.get("Exchange", "NSE").upper(),
            "Token": str(row.get("Token", "")).strip(),
            "Type": row.get("Type", "Holding"),
            "Quantity": float(row.get("Quantity", 0) or 0),
            "Average Price": float(row.get("Average Price", 0) or 0),
            "CMP": float(row.get("CMP", 0) or 0),
            "PC": float(row.get("PC", 0) or 0),
            "Day High": float(row.get("Day High", row.get("CMP", 0) or 0)),
            "Volume": float(row.get("Volume", 0) or 0),
            "Buy Date": row.get("Buy Date"),
            "Buy Price": row.get("Buy Price"),
            "Sell Price": row.get("Sell Price"),
            "Side": row.get("Side", "LONG"),
            "Status": row.get("Status", "OPEN"),
            "PD Volume": row.get("PD Volume", 0),
            "Broker": self.name,
        }
        return normalized
