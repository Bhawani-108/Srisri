from __future__ import annotations

import os
import csv
import io
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import requests
import pandas as pd
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
            elif resp.status_code == 401:
                print("❌ [INDMONEY ERROR] 401 Unauthorized: Access token in secrets.toml has EXPIRED.")
                return None
            else:
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
        if self._equity_instruments is not None and len(self._equity_instruments) > 0:
            return self._equity_instruments

        instrument_map: Dict[tuple[str, str], str] = {}
        if not self.access_token:
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
                    display_name = str(row.get("SYMBOL_NAME") or row.get("CUSTOM_SYMBOL") or "").strip()
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
                self._equity_instruments = instrument_map
        except Exception:
            pass

        return instrument_map

    def get_equity_instrument_catalog(self) -> List[Dict[str, str]]:
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

    def fetch_daily_baselines(self, tokens_or_symbols: Optional[List[str]] = None) -> Dict[str, Dict[str, float]]:
        """Extracts historical daily close prices and computes consecutive day-over-day percentage changes."""
        if not tokens_or_symbols:
            return {}

        instrument_map = self._get_equity_instrument_map()
        codes = []
        token_to_key = {}

        for item in tokens_or_symbols:
            code = str(item).strip()
            if not code:
                continue
            
            # Resolve symbol (e.g. "ACE") to numerical security ID using instrument map
            sec_id = code
            if not code.isdigit() and "_" not in code:
                sec_id = instrument_map.get(("NSE", code), instrument_map.get(("BSE", code), ""))
                if not sec_id:
                    for (exch, sym), s_id in instrument_map.items():
                        if sym == code:
                            sec_id = s_id
                            break
            
            # If we still don't have a valid numeric security ID, skip or try using code as is if numeric
            if not sec_id:
                sec_id = code

            scrip_code = sec_id if "_" in sec_id else f"NSE_{sec_id}"
            codes.append(scrip_code)
            raw_t = scrip_code.split("_")[-1]
            token_to_key[raw_t] = code
            token_to_key[scrip_code] = code
            token_to_key[code] = code

        codes = list(dict.fromkeys(codes))
        if not codes:
            return {}

        baselines: Dict[str, Dict[str, float]] = {}
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (60 * 24 * 60 * 60 * 1000)
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        today_ist = datetime.now(ist_tz).date()
        current_month_start = today_ist.replace(day=1)

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
            time.sleep(0.08)

            # Fallback to individual requests if batch fails
            if not isinstance(payload, dict) or not payload.get("data"):
                for scrip_code in batch:
                    single_payload = self._request(
                        "GET",
                        "/market/historical/1day",
                        params={
                            "scrip-codes": scrip_code,
                            "start_time": start_ms,
                            "end_time": now_ms,
                        },
                    )
                    if isinstance(single_payload, dict) and single_payload.get("data"):
                        if not isinstance(payload, dict):
                            payload = {"data": {}}
                        if isinstance(payload.get("data"), dict):
                            payload["data"].update(single_payload["data"])
                    time.sleep(0.05)

            if not isinstance(payload, dict):
                continue

            data_block = payload.get("data", {})
            if not isinstance(data_block, dict):
                continue

            for scrip_key, instrument in data_block.items():
                candles = []
                if isinstance(instrument, dict):
                    candles = instrument.get("candles") or instrument.get("data") or []
                elif isinstance(instrument, list):
                    candles = instrument

                if not isinstance(candles, list) or not candles:
                    continue

                candles_by_date = []
                for candle in candles:
                    try:
                        if isinstance(candle, dict):
                            close = float(candle.get("c") or candle.get("close") or candle.get("Close") or 0)
                            ts = float(candle.get("ts") or candle.get("timestamp") or candle.get("time") or 0)
                        elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
                            ts = float(candle[0])
                            close = float(candle[4])
                        else:
                            continue

                        if ts <= 0 or close <= 0:
                            continue
                        ts_sec = ts / 1000.0 if ts > 1e11 else ts
                        c_date = datetime.fromtimestamp(ts_sec, ist_tz).date()
                        candles_by_date.append((c_date, close))
                    except Exception:
                        continue

                if not candles_by_date:
                    continue

                candles_by_date.sort(key=lambda x: x[0])
                closed_candles = [c for c in candles_by_date if c[0] < today_ist]
                if not closed_candles:
                    closed_candles = candles_by_date

                previous_month_candles = [
                    c for c in closed_candles if c[0] < current_month_start
                ]
                pmc = previous_month_candles[-1][1] if previous_month_candles else 0.0
                
                n = len(closed_candles)
                if n >= 6:
                    c_t1 = closed_candles[-1][1]  # Latest closed session
                    c_t2 = closed_candles[-2][1]  # 1D% session close
                    c_t3 = closed_candles[-3][1]  # 2D% session close
                    c_t4 = closed_candles[-4][1]  # 3D% session close
                    c_t5 = closed_candles[-5][1]  # 4D% session close
                    c_t6 = closed_candles[-6][1]

                    pct_1d = ((c_t2 - c_t3) / c_t3) * 100 if c_t3 > 0 else 0.0
                    pct_2d = ((c_t3 - c_t4) / c_t4) * 100 if c_t4 > 0 else 0.0
                    pct_3d = ((c_t4 - c_t5) / c_t5) * 100 if c_t5 > 0 else 0.0
                    pct_4d = ((c_t5 - c_t6) / c_t6) * 100 if c_t6 > 0 else 0.0
                else:
                    pct_1d = pct_2d = pct_3d = pct_4d = 0.0

                raw_token = scrip_key.split("_")[-1]
                entry = {
                    "1D%": pct_1d,
                    "2D%": pct_2d,
                    "3D%": pct_3d,
                    "4D%": pct_4d,
                    "PMC": pmc,
                }
                
                baselines[raw_token] = entry
                baselines[scrip_key] = entry
                if raw_token in token_to_key:
                    baselines[token_to_key[raw_token]] = entry

        return baselines