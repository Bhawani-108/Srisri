import time
import threading
import requests
import pyotp
import pandas as pd
import os
import tempfile
from datetime import datetime
import streamlit as st
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

# ==========================================
# CONFIGURATION & BROKER CREDENTIALS
# ==========================================
ANGEL_API_KEY = st.secrets["ANGEL_API_KEY"]
ANGEL_CLIENT_ID = st.secrets["ANGEL_CLIENT_ID"]
ANGEL_PASSWORD = st.secrets["ANGEL_PASSWORD"]
ANGEL_TOTP_SECRET = st.secrets["ANGEL_TOTP_SECRET"]

CSV_FILE = "portfolio.csv"
WATCHLIST_FILE = "watchlist.txt"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

if not os.path.exists(WATCHLIST_FILE):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("")

if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, "w", encoding="utf-8") as f:
        f.write("")

# ==========================================
# BROKER AUTHENTICATION
# ==========================================
smart_connect = SmartConnect(api_key=ANGEL_API_KEY)
totp = pyotp.TOTP(ANGEL_TOTP_SECRET).now()

session = smart_connect.generateSession(ANGEL_CLIENT_ID, ANGEL_PASSWORD, totp)
if not session.get('status'):
    print(f"Login Failed: {session.get('message')}")
    exit()

print("Successfully authenticated with Angel One SmartAPI!")

FEED_TOKEN = smart_connect.feed_token
JWT_TOKEN = session.get('data', {}).get('jwtToken')
if not FEED_TOKEN:
    FEED_TOKEN = session.get('data', {}).get('feedToken')

# ==========================================
# TOKEN RESOLVER & SCRIP MASTER
# ==========================================
def infer_symbol_segment(symbol: str, exchange: str | None = None) -> str:
    text = str(symbol or "").upper().strip()
    if not text: return "EQ"

    suffix = text.rsplit('-', 1)[-1] if '-' in text else text
    suffix = suffix.upper()

    if "ETF" in text or "BEES" in text: return "ETF"
    if "MF" in text or "MUTF" in text or "MFS" in text: return "MF"
    if suffix in {"EQ", "BE", "BZ", "SM"}: return "EQ"
    if any(tag in suffix for tag in ("FUT", "OPT", "CE", "PE")): return "F&O"
    if any(tag in suffix for tag in ("COM", "CMD", "GOLD", "SILVER", "CRUDE")): return "COM"
    if any(tag in suffix for tag in ("CUR", "USD", "INR")): return "CUR"
    if exchange and str(exchange).upper() in {"NFO"}: return "F&O"
    if exchange and str(exchange).upper() in {"MCX"}: return "COM"
    return "EQ"

def load_scrip_master():
    print("Downloading active Scrip Master from Angel One...")
    try:
        res = requests.get(SCRIP_MASTER_URL, timeout=25)
        res.raise_for_status()
        scrip_data = res.json()

        token_map, exchange_map, segment_map, base_name_map = {}, {}, {}, {}

        for item in scrip_data:
            exch = str(item.get('exch_seg', '')).upper()
            if exch not in {'NSE', 'BSE', 'NFO', 'MCX', 'CDS', 'BFO', 'NCO'}:
                continue

            sym = str(item.get('symbol', '')).strip()
            if not sym: continue

            clean_sym = sym.split('-')[0].upper()
            token = str(item.get('token', '')).strip()
            native_name = str(item.get('name', '')).strip()

            if not token: continue

            unique_key = f"{clean_sym}:{exch}"

            if unique_key not in token_map:
                token_map[unique_key] = token
                exchange_map[unique_key] = exch
                segment_map[unique_key] = infer_symbol_segment(sym, exch)
                base_name_map[unique_key] = native_name

        print(f"Scrip Master indexed: {len(token_map)} active instruments.")
        return token_map, exchange_map, segment_map, base_name_map
    except Exception as e:
        print(f"Failed to fetch Scrip Master: {e}")
        return {}, {}, {}, {}


_MASTER_MAP = load_scrip_master()
TOKEN_MAP = _MASTER_MAP[0] if isinstance(_MASTER_MAP, tuple) and len(_MASTER_MAP) >= 1 else {}
EXCHANGE_MAP = _MASTER_MAP[1] if isinstance(_MASTER_MAP, tuple) and len(_MASTER_MAP) >= 2 else {}
SEGMENT_MAP = _MASTER_MAP[2] if isinstance(_MASTER_MAP, tuple) and len(_MASTER_MAP) >= 3 else {}
BASE_NAME_MAP = _MASTER_MAP[3] if isinstance(_MASTER_MAP, tuple) and len(_MASTER_MAP) >= 4 else {}
LAST_WATCHLIST_MTIME = 0

# ==========================================
# WEBSOCKET ENGINE (EVENT-DRIVEN FEED)
# ==========================================
LIVE_TICKS = {}
WS_APP = None
SUBSCRIBED_TOKENS = set()

def map_exch_to_ws_type(exch):
    mapping = {"NSE": 1, "NFO": 2, "BSE": 3, "MCX": 4, "NCDEX": 5, "CDS": 7}
    return mapping.get(str(exch).upper(), 1)

def on_data(wsapp, msg):
    try:
        if isinstance(msg, dict) and 'token' in msg:
            token = str(msg['token'])
            ltp = float(msg.get('last_traded_price', 0)) / 100.0
            close = float(msg.get('close_price', 0)) / 100.0
            high = float(msg.get('high_price_of_the_day', 0)) / 100.0
            vol = int(msg.get('volume_trade_for_the_day', 0))
            
            if ltp > 0:
                LIVE_TICKS[token] = {
                    'CMP': ltp,
                    'PC': close if close > 0 else ltp,
                    'Day High': high,
                    'Volume': vol
                }
    except Exception: pass

def on_open(wsapp): print("🟢 WebSocket Connection Established.")
def on_error(wsapp, error): print(f"🔴 WebSocket Error: {error}")
def on_close(wsapp): print("⚪ WebSocket Connection Closed. Reconnecting...")

def run_websocket():
    global WS_APP
    while True:
        try:
            WS_APP = SmartWebSocketV2(JWT_TOKEN, ANGEL_API_KEY, ANGEL_CLIENT_ID, FEED_TOKEN)
            WS_APP.on_open = on_open
            WS_APP.on_data = on_data
            WS_APP.on_error = on_error
            WS_APP.on_close = on_close
            WS_APP.connect()
        except Exception as e:
            print(f"WebSocket init failed: {e}. Retrying in 5s...")
            time.sleep(5)

threading.Thread(target=run_websocket, daemon=True).start()

def sync_ws_subscriptions(df):
    global SUBSCRIBED_TOKENS
    if df is None or df.empty or WS_APP is None: return

    current_tokens = set()
    exchange_groups = {}
    
    for _, row in df.iterrows():
        tok = str(row.get('Token', '')).strip()
        exch = str(row.get('Exchange', 'NSE')).strip()
        if tok and exch:
            current_tokens.add(tok)
            exch_code = map_exch_to_ws_type(exch)
            if exch_code not in exchange_groups: exchange_groups[exch_code] = []
            if tok not in exchange_groups[exch_code]: exchange_groups[exch_code].append(tok)

    new_tokens = current_tokens - SUBSCRIBED_TOKENS
    if new_tokens:
        token_list = [{"exchangeType": k, "tokens": v} for k, v in exchange_groups.items()]
        WS_APP.subscribe("ws_feed", 3, token_list)
        SUBSCRIBED_TOKENS = current_tokens
        print(f"📡 Subscribed to {len(current_tokens)} instruments on WebSocket.")

# ==========================================
# PORTFOLIO SYNC & BATCH WRITER
# ==========================================
def parse_trade_datetime(value):
    if pd.isna(value) or not value: return None
    try: return pd.to_datetime(value).to_pydatetime()
    except Exception: return None

def get_latest_buy_metadata():
    buy_meta = {}
    try:
        trade_res = smart_connect.tradeBook()
        if trade_res and trade_res.get('status') and trade_res.get('data'):
            for item in trade_res['data']:
                sym = str(item.get('tradingsymbol', '')).split('-')[0].strip().upper()
                txn = str(item.get('transactiontype', '')).upper()
                if sym and txn in {'BUY', 'B'}:
                    buy_meta[sym] = {
                        'Buy Date': item.get('tradedatetime'),
                        'Buy Price': float(item.get('averageprice', 0))
                    }
    except Exception: pass
    return buy_meta

def sync_portfolio_registry(current_df):
    global LAST_WATCHLIST_MTIME
    mtime = 0
    try: mtime = os.stat(WATCHLIST_FILE).st_mtime_ns
    except FileNotFoundError: pass

    if current_df is None or mtime != LAST_WATCHLIST_MTIME:
        LAST_WATCHLIST_MTIME = mtime
        print("Syncing holding positions and updated watchlist entries...")

        combined = []
        seen_holdings = set()
        buy_metadata = get_latest_buy_metadata()

        try:
            h_res = smart_connect.holding()
            if h_res.get('status') and h_res.get('data'):
                for item in h_res['data']:
                    sym = str(item.get('tradingsymbol', '')).split('-')[0].strip().upper()
                    exch = str(item.get('exchange', 'NSE')).upper()
                    
                    unique_key = f"{sym}:{exch}"
                    tok = str(item.get('symboltoken', '')).strip()
                    if not tok or tok == "0": tok = TOKEN_MAP.get(unique_key, "")

                    buy_meta = buy_metadata.get(sym, {})
                    avg_price = float(item.get('averageprice', 0.0))

                    if sym and tok:
                        seen_holdings.add(unique_key)
                        combined.append({
                            'Stock Name': sym,
                            'Exchange': exch,
                            'Token': tok,
                            'Type': 'Holding',
                            'Quantity': float(item.get('quantity', 0)),
                            'Average Price': avg_price,
                            'Buy Date': buy_meta.get('Buy Date'),
                            'Buy Price': buy_meta.get('Buy Price', avg_price),
                        })
        except Exception as e:
            print(f"Holdings fetch notice: {e}")

        # Separate tracking for watchlist so items held in portfolio can also be watchlisted
        seen_watchlist = set()
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                watch_tickers = [line.strip().upper() for line in f if line.strip()]

            for entry in watch_tickers:
                if ":" in entry:
                    sym, exch = entry.split(":", 1)
                    unique_key = entry
                else:
                    sym = entry
                    exch = "NSE"
                    unique_key = f"{sym}:NSE"
                    if unique_key not in TOKEN_MAP:
                        unique_key = f"{sym}:BSE"
                        exch = "BSE"

                token = TOKEN_MAP.get(unique_key)
                if unique_key not in seen_watchlist and token:
                    combined.append({
                        'Stock Name': sym,
                        'Exchange': exch,
                        'Token': token,
                        'Type': 'Watchlist',
                        'Quantity': 0.0,
                        'Average Price': 0.0
                    })
                    seen_watchlist.add(unique_key)

        return pd.DataFrame(combined)
    return current_df


def stream_tick_cycle(df):
    if df is None or df.empty: return df
    sync_ws_subscriptions(df)

    try:
        t_series = df['Token'].astype(str)
        df['CMP'] = t_series.map(lambda t: LIVE_TICKS.get(t, {}).get('CMP')).fillna(df.get('CMP', 0.0))
        df['PC'] = t_series.map(lambda t: LIVE_TICKS.get(t, {}).get('PC')).fillna(df.get('PC', 0.0))
        df['Day High'] = t_series.map(lambda t: LIVE_TICKS.get(t, {}).get('Day High')).fillna(df.get('Day High', 0.0))
        df['Volume'] = t_series.map(lambda t: LIVE_TICKS.get(t, {}).get('Volume')).fillna(df.get('Volume', 0))

        df['D%'] = ((df['CMP'] - df['PC']) / df['PC'].replace(0, 1)) * 100
        df['DH%'] = ((df['Day High'] - df['PC']) / df['PC'].replace(0, 1)) * 100
        df['SAlert'] = ((df['CMP'] - df['Day High']) / df['CMP'].replace(0, 1)) * 100

        df['Total Invested'] = df['Quantity'] * df['Average Price']
        df['Current Value'] = df['Quantity'] * df['CMP']
        df['Net P&L'] = df['Current Value'] - df['Total Invested']
        df['ROI (%)'] = (df['Net P&L'] / df['Total Invested'].replace(0, 1)) * 100

        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(CSV_FILE)), suffix='.csv')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                df.to_csv(f, index=False)
            
            replaced = False
            for _ in range(5):
                try:
                    os.replace(temp_path, CSV_FILE)
                    replaced = True
                    break
                except PermissionError:
                    time.sleep(0.05)
        finally:
            if temp_path and os.path.exists(temp_path):
                try: os.remove(temp_path)
                except Exception: pass

    except Exception as e:
        print(f"CSV Batch update error: {e}")
    return df


if __name__ == "__main__":
    current_portfolio = None
    print("🚀 Booting event-driven market engine...")
    try:
        while True:
            current_portfolio = sync_portfolio_registry(current_portfolio)
            current_portfolio = stream_tick_cycle(current_portfolio)
            time.sleep(1.5)
    except KeyboardInterrupt:
        print("\nProcess halted by user.")