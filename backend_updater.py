import time
import threading
import requests
import numpy as np
import pandas as pd
import os
import tempfile
from datetime import datetime
import streamlit as st

from brokers.config import get_active_broker_name, get_broker_secrets
from brokers.manager import get_active_broker_adapter

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(APP_DIR, "watchlist.txt")
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
DATA_REFRESH_INTERVAL = 1.0

def get_csv_for_broker(broker_name: str) -> str:
    return os.path.join(APP_DIR, f"portfolio_{broker_name.lower().strip()}.csv")

def normalize_token(val) -> str:
    """Guarantee token is a clean integer string without float decimal artifacts (e.g. '3045.0' -> '3045')."""
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return "" if s.lower() in ("nan", "none", "") else s

if not os.path.exists(WATCHLIST_FILE):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("")

try:
    import pyotp
except Exception:
    pyotp = None

try:
    from SmartApi import SmartConnect
except Exception:
    SmartConnect = None

try:
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
except Exception:
    SmartWebSocketV2 = None

# ==========================================
# ANGEL ONE AUTHENTICATION
# ==========================================
smart_connect = None
JWT_TOKEN = None
FEED_TOKEN = None
AUTH_LOCK = threading.Lock()

def authenticate():
    global smart_connect, JWT_TOKEN, FEED_TOKEN

    # Safety Guard: Never attempt Angel One login if not running as angel_one
    active_broker = os.environ.get("ACTIVE_BROKER", get_active_broker_name()).lower()
    if active_broker != "angel_one":
        return None, None

    if SmartConnect is None or pyotp is None:
        print("❌ [ANGEL ONE] SmartConnect or pyotp module not installed.")
        return None, None

    cfg = get_broker_secrets("angel_one")
    api_key = cfg.get("api_key") or os.getenv("ANGEL_API_KEY", "")
    client_id = cfg.get("client_id") or os.getenv("ANGEL_CLIENT_ID", "")
    password = str(cfg.get("password") or os.getenv("ANGEL_PASSWORD", ""))
    totp_secret = cfg.get("totp_secret") or os.getenv("ANGEL_TOTP_SECRET", "")

    if not api_key or not client_id or not password or not totp_secret:
        return None, None

    with AUTH_LOCK:
        try:
            print(f"📡 [ANGEL ONE] Logging in client {client_id}...")
            smart_connect = SmartConnect(api_key=api_key)
            totp = pyotp.TOTP(totp_secret).now()
            session = smart_connect.generateSession(client_id, password, totp)
            if not session or not session.get('status'):
                print(f"❌ [ANGEL ONE] Session generation failed: {session.get('message') if session else 'None'}")
                return None, None
            FEED_TOKEN = smart_connect.feed_token or session.get('data', {}).get('feedToken')
            JWT_TOKEN = session.get('data', {}).get('jwtToken')
            print("🟢 Successfully authenticated with Angel One SmartAPI!")
            return JWT_TOKEN, FEED_TOKEN
        except Exception as e:
            print(f"❌ [ANGEL ONE] Auth exception: {e}")
            return None, None

def refresh_broker_session():
    global JWT_TOKEN, FEED_TOKEN
    active_broker = os.environ.get("ACTIVE_BROKER", get_active_broker_name()).lower()
    if active_broker != "angel_one":
        return False

    new_jwt, new_feed = authenticate()
    if new_jwt and new_feed:
        JWT_TOKEN = new_jwt
        FEED_TOKEN = new_feed
        return True
    return False

# ==========================================
# SCRIP MASTER
# ==========================================
TOKEN_MAP = {}
EXCHANGE_MAP = {}
SEGMENT_MAP = {}
BASE_NAME_MAP = {}
LAST_WATCHLIST_MTIME = 0
SCRIP_LOADED = False

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

def ensure_scrip_master():
    global TOKEN_MAP, EXCHANGE_MAP, SEGMENT_MAP, BASE_NAME_MAP, SCRIP_LOADED
    if SCRIP_LOADED and TOKEN_MAP:
        return
    try:
        print("Downloading active Scrip Master from Angel One...")
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
            token = normalize_token(item.get('token', ''))
            native_name = str(item.get('name', '')).strip()
            if not token: continue

            unique_key = f"{clean_sym}:{exch}"
            if unique_key not in token_map:
                token_map[unique_key] = token
                exchange_map[unique_key] = exch
                segment_map[unique_key] = infer_symbol_segment(sym, exch)
                base_name_map[unique_key] = native_name

        TOKEN_MAP = token_map
        EXCHANGE_MAP = exchange_map
        SEGMENT_MAP = segment_map
        BASE_NAME_MAP = base_name_map
        SCRIP_LOADED = True
        print(f"Scrip Master indexed: {len(token_map)} active instruments.")
    except Exception as e:
        print(f"Failed to fetch Scrip Master: {e}")

# ==========================================
# WEBSOCKET ENGINE & TICK CACHE
# ==========================================
LIVE_TICKS = {}
WS_APP = None
SUBSCRIBED_TOKENS = set()
IS_WS_READY = False
PENDING_EXCHANGE_GROUPS = {}

def load_persisted_ticks():
    """Seed LIVE_TICKS with known CMP, PC, Day High, and Volume from disk on app boot."""
    for broker in ("angel_one", "indmoney", "us_stocks"):
        csv_file = get_csv_for_broker(broker)
        if os.path.exists(csv_file) and os.path.getsize(csv_file) > 0:
            try:
                prev_df = pd.read_csv(csv_file, dtype={'Token': str})
                for _, row in prev_df.iterrows():
                    tok = normalize_token(row.get('Token', ''))
                    if not tok:
                        continue
                    try:
                        pc = float(row.get('PC', 0) or 0)
                        cmp_val = float(row.get('CMP', 0) or 0)
                        high = float(row.get('Day High', 0) or 0)
                        vol = int(float(row.get('Volume', 0) or 0))
                    except (ValueError, TypeError):
                        continue

                    if tok not in LIVE_TICKS:
                        LIVE_TICKS[tok] = {}
                    if pc > 0:
                        LIVE_TICKS[tok]['PC'] = pc
                    if cmp_val > 0 and 'CMP' not in LIVE_TICKS[tok]:
                        LIVE_TICKS[tok]['CMP'] = cmp_val
                    if high > 0 and 'Day High' not in LIVE_TICKS[tok]:
                        LIVE_TICKS[tok]['Day High'] = high
                    if vol > 0 and 'Volume' not in LIVE_TICKS[tok]:
                        LIVE_TICKS[tok]['Volume'] = vol
            except Exception as e:
                print(f"Warning seeding persisted ticks from {csv_file}: {e}")

load_persisted_ticks()

def fetch_angel_market_data(tokens_by_exchange):
    """Fetch official OHLC & Previous Close from Angel One REST API for missing tokens."""
    global smart_connect
    active_broker = os.environ.get("ACTIVE_BROKER", get_active_broker_name()).lower()
    if active_broker != "angel_one":
        return {}

    if not smart_connect:
        authenticate()
    if not smart_connect:
        return {}

    results = {}
    try:
        for exch, tokens in tokens_by_exchange.items():
            for i in range(0, len(tokens), 50):
                batch = tokens[i:i+50]
                payload = {exch: batch}
                res = smart_connect.getMarketData(mode="OHLC", exchangeTokens=payload)
                if res and res.get("status") and res.get("data"):
                    fetched = res["data"].get("fetched", [])
                    if isinstance(fetched, list):
                        for item in fetched:
                            tok = normalize_token(item.get("symbolToken") or item.get("token") or item.get("symboltoken") or "")
                            c = float(item.get("close") or item.get("prevClose") or item.get("closePrice") or 0.0)
                            l = float(item.get("ltp") or item.get("ltpPrice") or 0.0)
                            h = float(item.get("high", 0.0) or 0.0)
                            if tok and c > 0:
                                results[tok] = {"PC": c, "CMP": l, "Day High": h}
    except Exception as e:
        print(f"Error fetching market data from Angel One: {e}")
    return results

def map_exch_to_ws_type(exch):
    mapping = {"NSE": 1, "NFO": 2, "BSE": 3, "MCX": 4, "NCDEX": 5, "CDS": 7}
    return mapping.get(str(exch).upper(), 1)

def on_data(wsapp, msg):
    try:
        if isinstance(msg, dict) and 'token' in msg:
            token = normalize_token(msg.get('token'))
            ltp = float(msg.get('last_traded_price', 0)) / 100.0

            raw_close = (
                msg.get('closed_price')
                or msg.get('close_price')
                or msg.get('close')
                or msg.get('prev_close')
                or 0
            )
            close = float(raw_close) / 100.0 if float(raw_close) > 0 else 0.0
            high = float(msg.get('high_price_of_the_day', 0)) / 100.0
            vol = int(msg.get('volume_trade_for_the_day', 0))

            if ltp > 0 and token:
                prev_record = LIVE_TICKS.get(token, {})
                prev_pc = float(prev_record.get('PC', 0.0) or 0.0)

                if close > 0:
                    final_pc = close
                elif prev_pc > 0:
                    final_pc = prev_pc
                else:
                    final_pc = 0.0

                prev_high = float(prev_record.get('Day High', 0.0) or 0.0)
                final_high = high if high > 0 else (prev_high if prev_high > 0 else ltp)

                LIVE_TICKS[token] = {
                    'CMP': ltp,
                    'PC': final_pc,
                    'Day High': final_high,
                    'Volume': vol if vol > 0 else prev_record.get('Volume', 0)
                }
    except Exception:
        pass

def on_open(wsapp):
    global IS_WS_READY, SUBSCRIBED_TOKENS
    IS_WS_READY = True
    print("🟢 Angel One WebSocket Connection Established.")
    if PENDING_EXCHANGE_GROUPS:
        try:
            token_list = [{"exchangeType": k, "tokens": v} for k, v in PENDING_EXCHANGE_GROUPS.items()]
            wsapp.subscribe("ws_feed", 3, token_list)
            SUBSCRIBED_TOKENS = set([tok for grp in PENDING_EXCHANGE_GROUPS.values() for tok in grp])
            print(f"📡 Subscribed to {len(SUBSCRIBED_TOKENS)} tokens on WebSocket.")
        except Exception:
            pass

def on_error(wsapp, error):
    global IS_WS_READY
    IS_WS_READY = False

def on_close(wsapp):
    global IS_WS_READY
    IS_WS_READY = False

def run_websocket():
    global WS_APP, IS_WS_READY, SUBSCRIBED_TOKENS
    retry_delay = 5

    while True:
        try:
            active_broker = os.environ.get("ACTIVE_BROKER", get_active_broker_name()).lower()
            if active_broker != "angel_one":
                time.sleep(2)
                continue

            if not JWT_TOKEN or not FEED_TOKEN:
                refresh_broker_session()
                time.sleep(retry_delay)
                continue

            cfg = get_broker_secrets("angel_one")
            api_key = cfg.get("api_key") or os.getenv("ANGEL_API_KEY", "")
            client_id = cfg.get("client_id") or os.getenv("ANGEL_CLIENT_ID", "")

            IS_WS_READY = False
            SUBSCRIBED_TOKENS.clear()

            WS_APP = SmartWebSocketV2(JWT_TOKEN, api_key, client_id, FEED_TOKEN)
            WS_APP.on_open = on_open
            WS_APP.on_data = on_data
            WS_APP.on_error = on_error
            WS_APP.on_close = on_close

            WS_APP.connect()
            time.sleep(retry_delay)
        except Exception:
            IS_WS_READY = False
            time.sleep(retry_delay)
            if retry_delay >= 15:
                refresh_broker_session()
            retry_delay = min(retry_delay * 2, 60)

def start_ws_daemon():
    active_broker = os.environ.get("ACTIVE_BROKER", get_active_broker_name()).lower()
    if active_broker != "angel_one":
        return

    if not any(t.name == "angel_one_ws_thread" for t in threading.enumerate()):
        t = threading.Thread(target=run_websocket, name="angel_one_ws_thread", daemon=True)
        t.start()

def sync_ws_subscriptions(df):
    active_broker = os.environ.get("ACTIVE_BROKER", get_active_broker_name()).lower()
    if active_broker != "angel_one":
        return

    global SUBSCRIBED_TOKENS, PENDING_EXCHANGE_GROUPS
    if df is None or df.empty:
        return

    current_tokens = set()
    exchange_groups = {}
    for _, row in df.iterrows():
        tok = normalize_token(row.get('Token', ''))
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
        except Exception:
            pass

# ==========================================
# PORTFOLIO REGISTRY SYNC
# ==========================================
BROKER_CACHE = {}
INDMONEY_HISTORY_CACHE = {}
INDMONEY_HISTORY_FETCHED_AT = 0.0

def refresh_adapter_quotes(df, adapter):
    """Refresh broker quotes using tokens when available and symbols otherwise."""
    if df is None or df.empty:
        return

    request_keys = []
    for _, row in df.iterrows():
        token = normalize_token(row.get("Token", ""))
        if token:
            request_keys.append(token)
            continue
        symbol = str(row.get("Stock Name", "")).strip()
        exchange = str(row.get("Exchange", "NSE")).strip().upper()
        if symbol:
            request_keys.append(f"{exchange}_{symbol}")

    if not request_keys:
        return

    quotes = adapter.fetch_quotes(request_keys)
    token_map = {normalize_token(q.get("Token")): q for q in quotes if q.get("Token")}
    symbol_map = {
        (str(q.get("Exchange", "NSE")).upper(), str(q.get("Stock Name", "")).upper()): q
        for q in quotes
        if q.get("Stock Name")
    }

    for idx, row in df.iterrows():
        token = normalize_token(row.get("Token", ""))
        symbol_key = (
            str(row.get("Exchange", "NSE")).upper(),
            str(row.get("Stock Name", "")).upper(),
        )
        quote = token_map.get(token) if token else None
        quote = quote or symbol_map.get(symbol_key)
        if not quote:
            continue
        df.at[idx, "CMP"] = float(quote.get("CMP", 0) or 0)
        if float(quote.get("PC", 0) or 0) > 0:
            df.at[idx, "PC"] = float(quote["PC"])
        if float(quote.get("Day Open", 0) or 0) > 0:
            df.at[idx, "Day Open"] = float(quote["Day Open"])
        if float(quote.get("Day High", 0) or 0) > 0:
            df.at[idx, "Day High"] = float(quote["Day High"])
        df.at[idx, "Volume"] = float(quote.get("Volume", 0) or 0)

def refresh_indmoney_history(df, adapter):
    global INDMONEY_HISTORY_CACHE, INDMONEY_HISTORY_FETCHED_AT
    if df is None or df.empty or not hasattr(adapter, "fetch_daily_baselines"):
        return

    watchlist = df[df.get("Type", "") == "Watchlist"]
    tokens = [normalize_token(token) for token in watchlist.get("Token", []) if normalize_token(token)]
    tokens = list(dict.fromkeys(tokens))
    if not tokens:
        return

    now = time.time()
    if now - INDMONEY_HISTORY_FETCHED_AT >= 300 or not all(token in INDMONEY_HISTORY_CACHE for token in tokens):
        fetched = adapter.fetch_daily_baselines(tokens)
        if fetched:
            INDMONEY_HISTORY_CACHE.update(fetched)
            INDMONEY_HISTORY_FETCHED_AT = now

    for idx, row in watchlist.iterrows():
        token = normalize_token(row.get("Token", ""))
        baselines = INDMONEY_HISTORY_CACHE.get(token, {})
        current = float(row.get("CMP", 0) or 0)
        for name, baseline in baselines.items():
            df.at[idx, name] = ((current - baseline) / baseline) * 100 if baseline > 0 else 0.0
        day_open = float(row.get("Day Open", 0) or 0)
        if day_open > 0 and current > 0:
            df.at[idx, "1W%"] = ((current - day_open) / day_open) * 100

def sync_portfolio_registry(current_df=None):
    global LAST_WATCHLIST_MTIME, BROKER_CACHE
    active_broker = os.environ.get("ACTIVE_BROKER", get_active_broker_name()).lower()

    # --- INDMONEY / US STOCKS PATH ---
    if active_broker in ("indmoney", "us_stocks"):
        try:
            adapter = get_active_broker_adapter()
            pos = adapter.fetch_positions()
            wl = adapter.fetch_watchlist()
            combined = pos + wl
            if combined:
                df = pd.DataFrame(combined)
                refresh_adapter_quotes(df, adapter)
                if active_broker == "indmoney":
                    refresh_indmoney_history(df, adapter)
                BROKER_CACHE[active_broker] = df
                return df
        except Exception as e:
            print(f"[{active_broker.upper()}] sync error: {e}")
        return BROKER_CACHE.get(active_broker, pd.DataFrame())

    # --- ANGEL ONE PATH (Only executed if angel_one is explicitly active) ---
    if active_broker != "angel_one":
        return pd.DataFrame()

    ensure_scrip_master()

    mtime = 0
    try:
        mtime = os.stat(WATCHLIST_FILE).st_mtime_ns
    except FileNotFoundError:
        pass

    cached_angel = BROKER_CACHE.get("angel_one")
    if (
        cached_angel is not None 
        and not cached_angel.empty 
        and mtime == LAST_WATCHLIST_MTIME 
        and (pd.to_numeric(cached_angel.get('PC', 0), errors='coerce') > 0).all()
    ):
        return cached_angel

    LAST_WATCHLIST_MTIME = mtime
    combined = []
    seen_holdings = set()

    try:
        if smart_connect is None:
            authenticate()

        h_res = smart_connect.holding() if smart_connect else None
        if not h_res or not h_res.get('status'):
            refresh_broker_session()
            if smart_connect:
                h_res = smart_connect.holding()

        if h_res and h_res.get('status') and h_res.get('data'):
            for item in h_res['data']:
                sym = str(item.get('tradingsymbol', '')).split('-')[0].strip().upper()
                exch = str(item.get('exchange', 'NSE')).upper()
                unique_key = f"{sym}:{exch}"
                tok = normalize_token(item.get('symboltoken', '')) or TOKEN_MAP.get(unique_key, "")
                avg_price = float(item.get('averageprice', 0.0) or 0.0)
                
                raw_close = item.get('close') or item.get('closeprice') or item.get('prevClose') or item.get('previousclose') or 0.0
                close_price = float(raw_close or 0.0)
                ltp_price = float(item.get('ltp') or item.get('ltpPrice') or 0.0)

                if close_price <= 0 and tok in LIVE_TICKS:
                    close_price = float(LIVE_TICKS[tok].get('PC', 0.0) or 0.0)

                cmp_val = ltp_price if ltp_price > 0 else (close_price if close_price > 0 else avg_price)
                pc_val = close_price

                if sym and tok:
                    seen_holdings.add(unique_key)
                    combined.append({
                        'Stock Name': sym,
                        'Exchange': exch,
                        'Token': tok,
                        'Type': 'Holding',
                        'Quantity': float(item.get('quantity', 0)),
                        'Average Price': avg_price,
                        'CMP': cmp_val,
                        'PC': pc_val,
                        'Day High': cmp_val,
                        'Volume': 0,
                        'Buy Date': None,
                        'Buy Price': avg_price,
                        'Broker': 'angel_one'
                    })
            print(f"📊 [ANGEL ONE] Successfully loaded {len(combined)} holdings.")
    except Exception as e:
        print(f"❌ [ANGEL ONE] Holdings fetch exception: {e}")

    seen_watchlist = set()
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            watch_tickers = [line.strip().upper() for line in f if line.strip()]

        for entry in watch_tickers:
            sym = entry.split(":")[0]
            exch = entry.split(":")[1] if ":" in entry else "NSE"
            unique_key = f"{sym}:{exch}"
            token = TOKEN_MAP.get(unique_key)
            if unique_key not in seen_watchlist and token:
                cached_tick = LIVE_TICKS.get(token, {})
                cached_pc = float(cached_tick.get('PC', 0.0) or 0.0)
                cached_cmp = float(cached_tick.get('CMP', 0.0) or 0.0)
                cached_high = float(cached_tick.get('Day High', 0.0) or cached_cmp)
                cached_vol = float(cached_tick.get('Volume', 0.0) or 0.0)

                combined.append({
                    'Stock Name': sym,
                    'Exchange': exch,
                    'Token': token,
                    'Type': 'Watchlist',
                    'Quantity': 0.0,
                    'Average Price': 0.0,
                    'CMP': cached_cmp,
                    'PC': cached_pc,
                    'Day High': cached_high,
                    'Volume': cached_vol,
                    'Broker': 'angel_one'
                })
                seen_watchlist.add(unique_key)

    # Fetch missing PC for any token via REST OHLC
    missing_tokens_by_exch = {}
    for item in combined:
        if item.get('PC', 0.0) <= 0:
            tok = item.get('Token')
            exch = item.get('Exchange', 'NSE')
            if tok and exch:
                missing_tokens_by_exch.setdefault(exch, []).append(tok)

    if missing_tokens_by_exch and smart_connect:
        fetched_quotes = fetch_angel_market_data(missing_tokens_by_exch)
        for item in combined:
            tok = item.get('Token')
            if tok in fetched_quotes:
                q = fetched_quotes[tok]
                if q.get('PC', 0) > 0:
                    item['PC'] = q['PC']
                    if tok not in LIVE_TICKS:
                        LIVE_TICKS[tok] = {}
                    LIVE_TICKS[tok]['PC'] = q['PC']
                if q.get('CMP', 0) > 0 and item.get('CMP', 0) <= 0:
                    item['CMP'] = q['CMP']
                    LIVE_TICKS[tok]['CMP'] = q['CMP']
                if q.get('Day High', 0) > 0 and item.get('Day High', 0) <= 0:
                    item['Day High'] = q['Day High']
                    LIVE_TICKS[tok]['Day High'] = q['Day High']

    if combined:
        start_ws_daemon()
        df = pd.DataFrame(combined)
        BROKER_CACHE["angel_one"] = df
        return df

    return BROKER_CACHE.get("angel_one", pd.DataFrame())

def stream_tick_cycle(df):
    if df is None or df.empty:
        return df

    active_broker = os.environ.get("ACTIVE_BROKER", get_active_broker_name()).lower()

    if active_broker in ("indmoney", "us_stocks"):
        try:
            adapter = get_active_broker_adapter()
            refresh_adapter_quotes(df, adapter)
            if active_broker == "indmoney":
                refresh_indmoney_history(df, adapter)
        except Exception:
            pass
    else:
        sync_ws_subscriptions(df)
        t_series = df['Token'].apply(normalize_token)

        def _extract_live(tok, field):
            rec = LIVE_TICKS.get(str(tok), {})
            val = rec.get(field)
            if val is not None:
                try:
                    f = float(val)
                    if f > 0:
                        return f
                except (ValueError, TypeError):
                    pass
            return np.nan

        tick_cmp = t_series.map(lambda t: _extract_live(t, 'CMP'))
        df['CMP'] = tick_cmp.combine_first(pd.to_numeric(df.get('CMP'), errors='coerce')).fillna(0.0)

        tick_pc = t_series.map(lambda t: _extract_live(t, 'PC'))
        df['PC'] = tick_pc.combine_first(pd.to_numeric(df.get('PC'), errors='coerce')).fillna(0.0)

        tick_high = t_series.map(lambda t: _extract_live(t, 'Day High'))
        df['Day High'] = tick_high.combine_first(pd.to_numeric(df.get('Day High'), errors='coerce')).fillna(df['CMP'])

        tick_vol = t_series.map(lambda t: _extract_live(t, 'Volume'))
        df['Volume'] = tick_vol.combine_first(pd.to_numeric(df.get('Volume'), errors='coerce')).fillna(0)

    # Core Vectorized Calculations
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

    # Calculate 1D %, 1D High %, and Sell Alert
    df['D%'] = np.where(pc_series > 0, ((cmp_series - pc_series) / pc_series) * 100, 0.0)
    df['DH%'] = np.where(pc_series > 0, ((high_series - pc_series) / pc_series) * 100, 0.0)
    df['SAlert'] = np.where(cmp_series > 0, ((cmp_series - high_series) / cmp_series) * 100, 0.0)

    df['Total Invested'] = qty_series * avg_series
    df['Current Value'] = qty_series * cmp_series
    df['Net P&L'] = df['Current Value'] - df['Total Invested']
    invested_series = df['Total Invested']
    df['ROI (%)'] = np.where(invested_series > 0, (df['Net P&L'] / invested_series) * 100, 0.0)

    # Save to broker-specific CSV
    target_csv = get_csv_for_broker(active_broker)
    if not df.empty:
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=APP_DIR, suffix='.csv')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                df.to_csv(f, index=False)
            for _ in range(5):
                try:
                    os.replace(temp_path, target_csv)
                    break
                except PermissionError:
                    time.sleep(0.05)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    return df