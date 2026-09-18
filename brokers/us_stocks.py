from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
import pandas as pd
from .base import BrokerAdapter

class USStocksAdapter(BrokerAdapter):
    """Adapter for tracking US Equity holdings with live US quote feeds."""
    name = "us_stocks"

    def login(self) -> bool:
        return True

    def fetch_positions(self) -> List[Dict[str, Any]]:
        csv_file = "us_holdings.csv"
        if not os.path.exists(csv_file):
            sample = pd.DataFrame([
                {"Stock Name": "AAPL", "Quantity": 2, "Buy Price": 185.50, "Buy Date": "2024-01-15"},
                {"Stock Name": "NVDA", "Quantity": 5, "Buy Price": 110.00, "Buy Date": "2024-03-10"},
                {"Stock Name": "TSLA", "Quantity": 3, "Buy Price": 215.20, "Buy Date": "2024-02-20"}
            ])
            sample.to_csv(csv_file, index=False)

        try:
            df = pd.read_csv(csv_file)
        except Exception:
            return []

        records = []
        for _, row in df.iterrows():
            sym = str(row.get("Stock Name", "")).strip().upper()
            qty = float(row.get("Quantity", 0))
            buy_p = float(row.get("Buy Price", 0.0))
            if sym and qty > 0:
                records.append(self.normalize_row({
                    "Stock Name": sym,
                    "Exchange": "NASDAQ",
                    "Token": sym,
                    "Type": "Holding",
                    "Quantity": qty,
                    "Average Price": buy_p,
                    "Buy Price": buy_p,
                    "CMP": buy_p,
                    "PC": 0.0,  # Populated with true previous close in fetch_quotes
                    "Day High": buy_p,
                    "Volume": 0,
                    "Broker": "us_stocks"
                }))
        return records

    def fetch_watchlist(self) -> List[Dict[str, Any]]:
        return []

    def fetch_quotes(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if not symbols:
            return []
        try:
            import yfinance as yf
            tickers = yf.Tickers(" ".join(symbols))
            results = []
            for sym in symbols:
                fast = getattr(tickers.tickers.get(sym), "fast_info", None)
                if fast:
                    cmp_ = float(fast.last_price or 0.0)
                    pc_ = float(fast.previous_close or 0.0)  # Never fall back to cmp_
                    high_ = float(fast.day_high or cmp_)
                    vol_ = float(fast.last_volume or 0)
                    results.append(self.normalize_row({
                        "Stock Name": sym,
                        "Exchange": "NASDAQ",
                        "Token": sym,
                        "CMP": cmp_,
                        "PC": pc_,
                        "Day High": high_,
                        "Volume": vol_,
                        "Broker": "us_stocks"
                    }))
            return results
        except Exception:
            return []