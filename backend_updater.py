import time
import threading
import requests
import pyotp
import numpy as np
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
# BROKER AUTHENTICATION & TOKEN REFRESH
# ==========================================
smart_connect = SmartConnect(api_key=ANGEL_API_KEY)

def authenticate():
    try:
        totp = pyotp.TOTP(ANGEL_TOTP_SECRET).now()
        session = smart_connect.generateSession(ANGEL_CLIENT_ID, ANGEL_PASSWORD, totp)
        if not session.get('status'):
            print(f"Login Notice: {session.get('message')}")
            return None, None
        
        feed_token = smart_connect.feed_token or session.get('data', {}).get('feedToken')
        jwt_token = session.get('data', {}).get('jwtToken')
        print("Successfully authenticated with Angel One SmartAPI!")
        return jwt_token, feed_token
    except Exception as e:
        print(f"Authentication exception: {e}")
        return None, None

JWT_TOKEN, FEED_TOKEN = authenticate()

def refresh_broker_session():
    global JWT_TOKEN, FEED_TOKEN
    new_jwt, new_feed = authenticate()
    if new_jwt and new_feed:
        JWT_TOKEN = new_jwt
        FEED_TOKEN = new_feed
        print("Broker session tokens renewed.")
        return True
    return False

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
IS_WS_READY = False
PENDING_EXCHANGE_GROUPS = {}

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
                prev_record = LIVE_TICKS.get(token, {})
                prev_pc = prev_record.get('PC', 0.0)
                final_pc = close if close > 0 else (prev_pc if prev_pc > 0 else ltp)

                LIVE_TICKS[token] = {
                    'CMP': ltp,
                    'PC': final_pc,
                    'Day High': high if high > 0 else ltp,
                    'Volume': vol if vol > 0 else prev_record.get('Volume', 0)
                }
    except Exception:
        pass

def on_open(wsapp):
    global IS_WS_READY, SUBSCRIBED_TOKENS
    IS_WS_READY = True
    print("🟢 WebSocket Connection Established.")
    if PENDING_EXCHANGE_GROUPS:
        try:
            token_list = [{"exchangeType": k, "tokens": v} for k, v in PENDING_EXCHANGE_GROUPS.items()]
            wsapp.subscribe("ws_feed", 3, token_list)
            SUBSCRIBED_TOKENS = set([tok for grp in PENDING_EXCHANGE_GROUPS.values() for tok in grp])
            print(f"📡 Initialized subscriptions for {len(SUBSCRIBED_TOKENS)} instruments.")
        except Exception as e:
            pass

def on_error(wsapp, error):
    global IS_WS_READY
    IS_WS_READY = False
    print(f"⚠️ WebSocket Notice: {error}")

def on_close(wsapp):
    global IS_WS_READY
    IS_WS_READY = False
    print("🔌 WebSocket Closed. Reconnecting...")

def run_websocket():
    global WS_APP, IS_WS_READY, SUBSCRIBED_TOKENS
    retry_delay = 5

    while True:
        try:
            if not JWT_TOKEN or not FEED_TOKEN:
                refresh_broker_session()
                time.sleep(retry_delay)
                continue

            IS_WS_READY = False
            SUBSCRIBED_TOKENS.clear()

            WS_APP = SmartWebSocketV2(JWT_TOKEN, ANGEL_API_KEY, ANGEL_CLIENT_ID, FEED_TOKEN)
            WS_APP.on_open = on_open
            WS_APP.on_data = on_data
            WS_APP.on_error = on_error
            WS_APP.on_close = on_close

            WS_APP.connect()
            time.sleep(retry_delay)
        except Exception as e:
            IS_WS_READY = False
            print(f"WebSocket session fault: {e}. Retrying in {retry_delay}s...")
            time.sleep(retry_delay)
            if retry_delay >= 15:
                refresh_broker_session()
            retry_delay = min(retry_delay * 2, 60)

threading.Thread(target=run_websocket, daemon=True).start()

def sync_ws_subscriptions(df):
    global SUBSCRIBED_TOKENS, PENDING_EXCHANGE_GROUPS
    if df is None or df.empty:
        return

    current_tokens = set()
    exchange_groups = {}
    
    for _, row in df.iterrows():
        tok = str(row.get('Token', '')).strip()
        exch = str(row.get('Exchange', 'NSE')).strip()
        if tok and exch:
            current_tokens.add(tok)
            exch_code = map_exch_to_ws_type(exch)
            if exch_code not in exchange_groups:
                exchange_groups[exch_code] = []
            if tok not in exchange_groups[exch_code]:
                exchange_groups[exch_code].append(tok)

    PENDING_EXCHANGE_GROUPS = exchange_groups
    new_tokens = current_tokens - SUBSCRIBED_TOKENS

    is_socket_live = (
        IS_WS_READY and 
        WS_APP is not None and 
        hasattr(WS_APP, "wsapp") and 
        WS_APP.wsapp is not None and 
        getattr(WS_APP.wsapp, "sock", None) is not None and 
        getattr(WS_APP.wsapp.sock, "connected", False)
    )

    if new_tokens and is_socket_live:
        try:
            token_list = [{"exchangeType": k, "tokens": v} for k, v in exchange_groups.items()]
            WS_APP.subscribe("ws_feed", 3, token_list)
            SUBSCRIBED_TOKENS.update(current_tokens)
            print(f"📡 Subscribed to {len(current_tokens)} instruments on WebSocket.")
        except Exception as e:
            pass

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
    except Exception:
        pass
    return buy_meta

def sync_portfolio_registry(current_df):
    global LAST_WATCHLIST_MTIME
    mtime = 0
    try:
        mtime = os.stat(WATCHLIST_FILE).st_mtime_ns
    except FileNotFoundError:
        pass

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
                    if not tok or tok == "0":
                        tok = TOKEN_MAP.get(unique_key, "")

                    buy_meta = buy_metadata.get(sym, {})
                    avg_price = float(item.get('averageprice', 0.0))
                    close_price = float(item.get('close', 0.0))

                    if sym and tok:
                        seen_holdings.add(unique_key)
                        combined.append({
                            'Stock Name': sym,
                            'Exchange': exch,
                            'Token': tok,
                            'Type': 'Holding',
                            'Quantity': float(item.get('quantity', 0)),
                            'Average Price': avg_price,
                            'PC': close_price if close_price > 0 else avg_price,
                            'Buy Date': buy_meta.get('Buy Date'),
                            'Buy Price': buy_meta.get('Buy Price', avg_price),
                        })
        except Exception as e:
            print(f"Holdings fetch notice: {e}")

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
                        'Average Price': 0.0,
                        'PC': 0.0
                    })
                    seen_watchlist.add(unique_key)

        return pd.DataFrame(combined)
    return current_df

def stream_tick_cycle(df):
    if df is None or df.empty:
        return df
    sync_ws_subscriptions(df)

    try:
        t_series = df['Token'].astype(str)
        
        tick_cmp = t_series.map(lambda t: LIVE_TICKS.get(t, {}).get('CMP'))
        df['CMP'] = tick_cmp.combine_first(df.get('CMP', pd.Series(0.0, index=df.index))).fillna(0.0)

        tick_pc = t_series.map(lambda t: LIVE_TICKS.get(t, {}).get('PC'))
        df['PC'] = tick_pc.combine_first(df.get('PC', pd.Series(0.0, index=df.index))).fillna(0.0)

        tick_high = t_series.map(lambda t: LIVE_TICKS.get(t, {}).get('Day High'))
        df['Day High'] = tick_high.combine_first(df.get('Day High', pd.Series(0.0, index=df.index))).fillna(df['CMP'])

        tick_vol = t_series.map(lambda t: LIVE_TICKS.get(t, {}).get('Volume'))
        df['Volume'] = tick_vol.combine_first(df.get('Volume', pd.Series(0, index=df.index))).fillna(0)

        pc_series = pd.to_numeric(df['PC'], errors='coerce').fillna(0.0)
        cmp_series = pd.to_numeric(df['CMP'], errors='coerce').fillna(0.0)
        high_series = pd.to_numeric(df['Day High'], errors='coerce').fillna(cmp_series)
        qty_series = pd.to_numeric(df['Quantity'], errors='coerce').fillna(0.0)
        avg_series = pd.to_numeric(df['Average Price'], errors='coerce').fillna(0.0)

        df['CMP'] = cmp_series
        df['PC'] = pc_series
        df['Day High'] = high_series
        df['Quantity'] = qty_series
        df['Average Price'] = avg_series

        df['D%'] = np.where(pc_series > 0, ((cmp_series - pc_series) / pc_series) * 100, 0.0)
        df['DH%'] = np.where(pc_series > 0, ((high_series - pc_series) / pc_series) * 100, 0.0)
        df['SAlert'] = np.where(cmp_series > 0, ((cmp_series - high_series) / cmp_series) * 100, 0.0)

        df['Total Invested'] = qty_series * avg_series
        df['Current Value'] = qty_series * cmp_series
        df['Net P&L'] = df['Current Value'] - df['Total Invested']
        
        invested_series = df['Total Invested']
        df['ROI (%)'] = np.where(invested_series > 0, (df['Net P&L'] / invested_series) * 100, 0.0)

        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(CSV_FILE)), suffix='.csv')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                df.to_csv(f, index=False)
            
            for _ in range(5):
                try:
                    os.replace(temp_path, CSV_FILE)
                    break
                except PermissionError:
                    time.sleep(0.05)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

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