from __future__ import annotations

import os
import csv
import io
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import requests
import streamlit as st
from .base import BrokerAdapter


class IndMoneyAdapter(BrokerAdapter):
    """Production adapter for INDmoney / INDstocks REST API."""

    name = "indmoney"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.base_url = self.config.get("base_url", "https://api.indstocks.com").rstrip("/")
        self._equity_instruments: Optional[Dict[tuple[str, str], str]] = None
        self._equity_instrument_names: Dict[tuple[str, str], str] = {}
        self._equity_instrument_symbols: Dict[tuple[str, str], str] = {}
        
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

    def _get_equity_instrument_map(self) -> Dict[tuple[str, str], str]:
        """Load the INDstocks equity master used to resolve symbols to SECURITY_ID."""
        if self._equity_instruments is not None:
            return self._equity_instruments

        instrument_map: Dict[tuple[str, str], str] = {}
        if not self.access_token:
            self._equity_instruments = instrument_map
            return instrument_map

        try:
            response = requests.get(
                f"{self.base_url}/market/instruments",
                headers=self._headers(),
                params={"source": "equity"},
                timeout=self.timeout,
            )
            if response.status_code == 200:
                reader = csv.DictReader(io.StringIO(response.text))
                for row in reader:
                    exchange = str(row.get("EXCH", "")).strip().upper()
                    security_id = str(row.get("SECURITY_ID", "")).strip()
                    if not exchange or not security_id:
                        continue
                    display_name = str(
                        row.get("SYMBOL_NAME") or row.get("CUSTOM_SYMBOL") or ""
                    ).strip()
                    trading_symbol = str(row.get("TRADING_SYMBOL", "")).strip().upper()
                    instrument_key = (exchange, security_id)
                    if trading_symbol:
                        self._equity_instrument_symbols.setdefault(instrument_key, trading_symbol)
                    for field in ("TRADING_SYMBOL", "SYMBOL_NAME", "CUSTOM_SYMBOL"):
                        value = str(row.get(field, "")).strip().upper()
                        if value:
                            key = (exchange, value.split("-")[0])
                            instrument_map.setdefault(key, security_id)
                            if display_name:
                                self._equity_instrument_names.setdefault(key, display_name)
        except Exception:
            instrument_map = {}

        self._equity_instruments = instrument_map
        return instrument_map

    def get_equity_instrument_catalog(self) -> List[Dict[str, str]]:
        """Return searchable INDmoney equity symbols with display names."""
        instrument_map = self._get_equity_instrument_map()
        catalog = {}
        for (exchange, symbol), token in instrument_map.items():
            if exchange not in {"NSE", "BSE"} or not token:
                continue
            key = (exchange, token)
            canonical_symbol = self._equity_instrument_symbols.get(key, symbol)
            catalog[key] = {
                "exchange": exchange,
                "symbol": canonical_symbol,
                "token": token,
                "name": self._equity_instrument_names.get((exchange, canonical_symbol),
                    self._equity_instrument_names.get((exchange, symbol), canonical_symbol)),
            }
        return list(catalog.values())

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

        instrument_map = self._get_equity_instrument_map()
        records = []
        for ticker in tickers:
            sym = ticker.split(":")[0]
            exch = ticker.split(":")[1] if ":" in ticker else "NSE"
            security_id = instrument_map.get((exch, sym), "")
            records.append(self.normalize_row({
                "Stock Name": sym,
                "Exchange": exch,
                "Token": security_id,
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
                open_val = float(val.get("day_open") or 0.0)
                high_val = float(val.get("day_high") or val.get("high") or cmp_val)

                records.append(self.normalize_row({
                    "Stock Name": str(val.get("symbol", "")).upper(),
                    "Exchange": "NSE",
                    "Token": raw_token,
                    "CMP": cmp_val,
                    "PC": pc_val,
                    "Day Open": open_val,
                    "Day High": high_val,
                    "Volume": float(val.get("volume") or 0),
                    "Broker": "indmoney"
                }))
        return records

    def fetch_daily_baselines(self, tokens: Optional[List[str]] = None) -> Dict[str, Dict[str, float]]:
        """Return prior daily closes used for multi-day watchlist performance."""
        if not tokens:
            return {}

        codes = []
        for token in tokens:
            code = str(token).strip()
            if code:
                codes.append(code if "_" in code else f"NSE_{code}")

        baselines: Dict[str, Dict[str, float]] = {}
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (45 * 24 * 60 * 60 * 1000)
        for offset in range(0, len(codes), 5):
            batch = codes[offset:offset + 5]
            payload = self._request(
                "GET",
                "/market/historical/1day",
                params={
                    "scrip-codes": ",".join(batch),
                    "start_time": start_ms,
                    "end_time": now_ms,
                },
            )
            if not isinstance(payload, dict):
                continue

            data_block = payload.get("data", {})
            if not isinstance(data_block, dict):
                continue
            for scrip_key, instrument in data_block.items():
                candles = instrument.get("candles") if isinstance(instrument, dict) else None
                if not isinstance(candles, list):
                    continue
                candles_by_date = []
                for candle in candles:
                    try:
                        close = float(candle.get("c", 0) or 0)
                        candle_date = datetime.fromtimestamp(
                            int(candle.get("ts", 0)), timezone.utc
                        ).date()
                    except (AttributeError, TypeError, ValueError):
                        close = 0.0
                        candle_date = None
                    if close > 0 and candle_date is not None:
                        candles_by_date.append((candle_date, close))

                if len(candles_by_date) < 2:
                    continue
                raw_token = scrip_key.split("_")[-1]
                period_offsets = {
                    "1D%": 1,
                    "2D%": 2,
                    "3D%": 3,
                    "4D%": 4,
                    "1M%": 26,
                }
                baselines[raw_token] = {}
                for name, days in period_offsets.items():
                    baseline_index = max(0, len(candles_by_date) - days - 1)
                    baselines[raw_token][name] = candles_by_date[baseline_index][1]
        return baselines