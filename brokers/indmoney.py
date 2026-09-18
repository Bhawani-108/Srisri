from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
import requests
import streamlit as st
from .base import BrokerAdapter


class IndMoneyAdapter(BrokerAdapter):
    """Production adapter for INDmoney / INDstocks REST API."""

    name = "indmoney"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.base_url = "https://api.indstocks.com"
        
        token = ""
        if hasattr(st, "secrets"):
            token = st.secrets.get("INDMONEY_ACCESS_TOKEN", "")
            if not token and "indmoney" in st.secrets:
                token = st.secrets["indmoney"].get("access_token", "")
        if not token:
            token = self.config.get("access_token") or os.getenv("INDMONEY_ACCESS_TOKEN", "")
            
        self.access_token = str(token).strip()
        self.timeout = 10.0

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = self.access_token
        return headers

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None):
        if not self.access_token:
            return None
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    def _extract_items(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for k in ("data", "holdings", "positions", "result", "items"):
                val = payload.get(k)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
            if "symbol" in payload or "security_id" in payload:
                return [payload]
        return []

    def login(self) -> bool:
        return bool(self.access_token)

    def fetch_positions(self) -> List[Dict[str, Any]]:
        """Queries settled Demat holdings + open CNC equity positions."""
        records = []
        seen_syms = set()

        holdings_payload = self._request("GET", "/portfolio/holdings")
        holding_items = self._extract_items(holdings_payload)

        positions_payload = self._request("GET", "/portfolio/positions", params={"segment": "equity", "product": "cnc"})
        position_items = self._extract_items(positions_payload)

        all_items = holding_items + position_items

        for row in all_items:
            sym = str(row.get("symbol") or row.get("trading_symbol") or "").split("-")[0].upper()
            sec_id = str(row.get("security_id") or row.get("scrip_code") or row.get("token") or "").strip()
            qty = float(row.get("total_qty") or row.get("net_qty") or row.get("quantity") or 0)
            avg_p = float(row.get("avg_price") or row.get("average_price") or 0.0)
            raw_close = row.get("prev_close") or row.get("close_price") or row.get("close") or 0.0
            pc_val = float(raw_close or 0.0)
            raw_cmp = row.get("live_price") or row.get("ltp") or 0.0
            cmp_val = float(raw_cmp or (pc_val if pc_val > 0 else avg_p))

            if sym and qty > 0 and sym not in seen_syms:
                seen_syms.add(sym)
                records.append(self.normalize_row({
                    "Stock Name": sym,
                    "Exchange": str(row.get("exchange", "NSE")).upper(),
                    "Token": sec_id,
                    "Type": "Holding",
                    "Quantity": qty,
                    "Average Price": avg_p,
                    "Buy Price": avg_p,
                    "CMP": cmp_val,
                    "PC": pc_val,
                    "Day High": cmp_val,
                    "Volume": 0,
                    "Broker": "indmoney"
                }))
        return records

    def fetch_watchlist(self) -> List[Dict[str, Any]]:
        watchlist_file = "watchlist.txt"
        if not os.path.exists(watchlist_file):
            return []

        with open(watchlist_file, "r", encoding="utf-8") as f:
            tickers = [line.strip().upper() for line in f if line.strip()]

        records = []
        for ticker in tickers:
            sym = ticker.split(":")[0]
            exch = ticker.split(":")[1] if ":" in ticker else "NSE"
            records.append(self.normalize_row({
                "Stock Name": sym,
                "Exchange": exch,
                "Token": "",
                "Type": "Watchlist",
                "Quantity": 0.0,
                "Average Price": 0.0,
                "CMP": 0.0,
                "PC": 0.0,
                "Day High": 0.0,
                "Volume": 0,
                "Broker": "indmoney"
            }))
        return records

    def fetch_quotes(self, tokens_or_symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if not tokens_or_symbols:
            return []

        scrip_list = []
        for item in tokens_or_symbols:
            code = str(item).strip()
            if not code:
                continue
            if "_" in code:
                scrip_list.append(code)
            else:
                scrip_list.append(f"NSE_{code}")

        payload = self._request("GET", "/market/quotes/full", params={"scrip-codes": ",".join(scrip_list[:50])})
        if not payload or not isinstance(payload, dict):
            return []

        data_block = payload.get("data", {})
        records = []

        if isinstance(data_block, dict):
            for scrip_key, val in data_block.items():
                if not isinstance(val, dict):
                    continue
                raw_token = scrip_key.split("_")[-1]
                cmp_val = float(val.get("live_price") or val.get("ltp") or 0.0)
                pc_val = float(val.get("prev_close") or val.get("close") or 0.0)
                high_val = float(val.get("day_high") or val.get("high") or cmp_val)

                records.append(self.normalize_row({
                    "Stock Name": str(val.get("symbol", "")).upper(),
                    "Exchange": "NSE",
                    "Token": raw_token,
                    "CMP": cmp_val,
                    "PC": pc_val,
                    "Day High": high_val,
                    "Volume": float(val.get("volume") or 0),
                    "Broker": "indmoney"
                }))
        return records