import io
import re
import requests
import pandas as pd
import streamlit as st
import backend_updater
from brokers.config import get_active_broker_name
from brokers.manager import get_active_broker_adapter

@st.cache_data(ttl=86400)
def get_universal_name_map():
    name_map = getattr(backend_updater, "BASE_NAME_MAP", {}).copy()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        eq_res = requests.get("https://archives.nseindia.com/content/equities/EQUITY_L.csv", headers=headers, timeout=10)
        if eq_res.status_code == 200:
            df_eq = pd.read_csv(io.StringIO(eq_res.text))
            df_eq.columns = df_eq.columns.str.strip()
            if 'SYMBOL' in df_eq.columns and 'NAME OF COMPANY' in df_eq.columns:
                stock_dict = {f"{str(sym).strip()}:NSE": str(name).strip() for sym, name in zip(df_eq['SYMBOL'], df_eq['NAME OF COMPANY'])}
                name_map.update(stock_dict)
    except Exception: pass

    try:
        etf_res = requests.get("https://archives.nseindia.com/content/equities/eq_etfseclist.csv", headers=headers, timeout=10)
        if etf_res.status_code == 200:
            df_etf = pd.read_csv(io.StringIO(etf_res.text))
            df_etf.columns = df_etf.columns.str.strip()
            if 'Symbol' in df_etf.columns and 'Security Name' in df_etf.columns:
                etf_dict = {f"{str(sym).strip()}:NSE": str(name).strip() for sym, name in zip(df_etf['Symbol'], df_etf['Security Name'])}
                name_map.update(etf_dict)
    except Exception: pass

    return name_map

def parse_angel_fo_symbol(symbol, base_name):
    if not base_name or not symbol.startswith(base_name): return "Derivative Contract", "F&O"
    remainder = symbol[len(base_name):]
    match = re.match(r'^(\d{2}[A-Z]{3}\d{2})(.*)$', remainder)
    if match:
        expiry = match.group(1)
        rest = match.group(2)
        exp_fmt = f"{expiry[:2]}-{expiry[2:5].capitalize()}-{expiry[5:]}"
        if 'FUT' in rest: return f"{exp_fmt} FUT", "FUT"
        elif rest.endswith('CE') or rest.endswith('PE'):
            opt_type = rest[-2:]
            strike = rest[:-2]
            if strike.endswith('00') and len(strike) > 3: strike = strike[:-2] + ".00"
            elif strike.endswith('0') and len(strike) > 3: strike = strike[:-1] + ".0"
            return f"{exp_fmt} {strike} {opt_type}", "OPT"
    return "Derivative Contract", "F&O"

@st.cache_data(ttl=3600)
def get_all_indexed_symbols(broker_name=None):
    active_broker = str(broker_name or get_active_broker_name()).strip().lower()

    if active_broker == "indmoney":
        adapter = get_active_broker_adapter()
        instrument_catalog = adapter.get_equity_instrument_catalog()
        items = []
        for instrument in instrument_catalog:
            exchange = instrument["exchange"]
            symbol = instrument["symbol"]
            token = instrument["token"]
            name = instrument["name"]
            if not symbol or not token:
                continue
            items.append({
                "unique_key": f"{symbol}:{exchange}",
                "symbol": symbol,
                "exchange": exchange,
                "segment": "EQ",
                "name": name,
                "label": f"{symbol} [{exchange} EQ] • {name}",
                "search_key": f"{symbol} {name} {exchange}".lower(),
            })
        return sorted(items, key=lambda item: (item["exchange"], item["symbol"]))

    master = getattr(backend_updater, "TOKEN_MAP", {}) or {}
    exchange_map = getattr(backend_updater, "EXCHANGE_MAP", {}) or {}
    segment_map = getattr(backend_updater, "SEGMENT_MAP", {}) or {}
    universal_names = get_universal_name_map()

    items = []
    seen = set()
    for unique_key, token in (master or {}).items():
        if ":" not in unique_key: continue
        clean, exchange = unique_key.split(":", 1)
        
        if not clean or not str(token).strip() or unique_key in seen: continue
        seen.add(unique_key)

        segment = str(segment_map.get(unique_key, "EQ")).upper()
        full_name = universal_names.get(unique_key, "")

        if segment == "F&O":
            sub_text, fo_tag = parse_angel_fo_symbol(clean, full_name)
            display_exch = "NSE FO" if exchange == "NFO" else exchange
            label = f"{clean} [{display_exch} {fo_tag}] • {sub_text}"
        else:
            sub_text = full_name if (full_name and full_name.upper() != clean) else "Equity"
            label = f"{clean} [{exchange} {segment}] • {sub_text}"

        search_key = f"{clean} {full_name} {sub_text}".lower()
        items.append({
            "unique_key": unique_key, "symbol": clean, "exchange": exchange,
            "segment": segment, "name": full_name, "label": label, "search_key": search_key
        })

    exchange_rank = {"NSE": 0, "BSE": 1, "NFO": 2, "MCX": 3, "CDS": 4, "NCO": 5, "BFO": 6}
    segment_rank = {"EQ": 0, "ETF": 1, "MF": 2, "F&O": 3, "COM": 4, "CUR": 5}
    items.sort(key=lambda x: (exchange_rank.get(x["exchange"], 99), segment_rank.get(x["segment"], 99), x["symbol"]))
    return items