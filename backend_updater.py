import time
import requests
import pyotp
import pandas as pd
import os
import tempfile
import streamlit as st
from SmartApi import SmartConnect

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

# Initialize files if not present
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

# ==========================================
# TOKEN RESOLVER (SUPPORTS -EQ, -BE, -BZ, -SM)
# ==========================================
def load_scrip_master():
    print("Downloading active Scrip Master from Angel One...")
    try:
        res = requests.get(SCRIP_MASTER_URL, timeout=25)
        res.raise_for_status()
        scrip_data = res.json()
        
        token_map = {}
        for item in scrip_data:
            if item.get('exch_seg') == 'NSE':
                sym = str(item.get('symbol', ''))
                if sym.endswith(('-EQ', '-BE', '-BZ', '-SM')):
                    clean_sym = sym.split('-')[0]
                    if clean_sym not in token_map or sym.endswith('-EQ'):
                        token_map[clean_sym] = str(item['token'])
                        
        print(f"Scrip Master indexed: {len(token_map)} active NSE instruments.")
        return token_map
    except Exception as e:
        print(f"Failed to fetch Scrip Master: {e}")
        return {}

TOKEN_MAP = load_scrip_master()
LAST_WATCHLIST_MTIME = 0

# ==========================================
# SYNC HOLDINGS & WATCHLIST
# ==========================================
def sync_portfolio_registry(current_df):
    global LAST_WATCHLIST_MTIME
    mtime = os.path.getmtime(WATCHLIST_FILE) if os.path.exists(WATCHLIST_FILE) else 0

    if current_df is None or mtime != LAST_WATCHLIST_MTIME:
        LAST_WATCHLIST_MTIME = mtime
        print("Syncing holding positions and updated watchlist entries...")

        combined = []
        seen = set()

        try:
            h_res = smart_connect.holding()
            if h_res.get('status') and h_res.get('data'):
                for item in h_res['data']:
                    sym = str(item.get('tradingsymbol', '')).split('-')[0].strip().upper()
                    tok = str(item.get('symboltoken', '')).strip()
                    if not tok or tok == "0":
                        tok = TOKEN_MAP.get(sym, "")

                    buy_date = (
                        item.get('buydate') or item.get('buyDate') or item.get('purchaseDate')
                        or item.get('purchasedate') or item.get('createddate') or item.get('createdDate')
                    )
                    buy_price = (
                        item.get('buyprice') or item.get('buyPrice') or item.get('averageprice')
                        or item.get('averagePrice') or item.get('lastbuyprice') or item.get('lastBuyPrice')
                    )

                    if sym and tok:
                        seen.add(sym)
                        combined.append({
                            'Stock Name': sym,
                            'Exchange': 'NSE',
                            'Token': tok,
                            'Type': 'Holding',
                            'Quantity': float(item.get('quantity', 0)),
                            'Average Price': float(buy_price if buy_price not in (None, '', '0') else 0.0),
                            'Buy Date': buy_date,
                            'Buy Price': float(buy_price if buy_price not in (None, '', '0') else 0.0),
                        })
        except Exception as e:
            print(f"Holdings fetch notice: {e}")

        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                watch_tickers = [line.strip().upper() for line in f if line.strip()]

            for sym in watch_tickers:
                if sym not in seen and sym in TOKEN_MAP:
                    combined.append({
                        'Stock Name': sym,
                        'Exchange': 'NSE',
                        'Token': TOKEN_MAP[sym],
                        'Type': 'Watchlist',
                        'Quantity': 0.0,
                        'Average Price': 0.0
                    })
                    seen.add(sym)

        new_df = pd.DataFrame(combined)

        if current_df is not None and not current_df.empty:
            cols = ['Token', 'CMP', 'PC', 'Day High', 'Volume', 'D%', 'DH%', 'SAlert']
            existing_cols = [c for c in cols if c in current_df.columns]
            if len(existing_cols) > 1:
                new_df = new_df.merge(current_df[existing_cols], on='Token', how='left')

        return new_df

    return current_df

# ==========================================
# FULL MARKET DATA STREAM (BATCHED)
# ==========================================
def stream_tick_cycle(df):
    if df is None or df.empty:
        return df

    try:
        tokens_list = [
            (str(row['Exchange']), str(row['Token']))
            for _, row in df.iterrows()
            if pd.notna(row.get('Token')) and str(row.get('Token')).strip() != ""
        ]

        cmp_map, pc_map, high_map, vol_map = {}, {}, {}, {}
        BATCH_SIZE = 50

        for i in range(0, len(tokens_list), BATCH_SIZE):
            batch = tokens_list[i:i + BATCH_SIZE]
            exchange_tokens = {}
            for exch, tok in batch:
                exchange_tokens.setdefault(exch, []).append(tok)

            res = smart_connect.getMarketData("FULL", exchange_tokens)
            if res.get('status') and res.get('data'):
                for item in res['data'].get('fetched', []):
                    t = str(item['symbolToken'])
                    cmp_map[t] = float(item['ltp'])
                    pc_map[t] = float(item['close'])
                    high_map[t] = float(item['high'])
                    vol_map[t] = int(item.get('tradeVolume', 0))

            if len(tokens_list) > BATCH_SIZE:
                time.sleep(1.0) 

        t_series = df['Token'].astype(str)
        df['CMP'] = t_series.map(cmp_map).fillna(df.get('CMP', 0.0))
        df['PC'] = t_series.map(pc_map).fillna(df.get('PC', 0.0))
        df['Day High'] = t_series.map(high_map).fillna(df.get('Day High', 0.0))
        df['Volume'] = t_series.map(vol_map).fillna(df.get('Volume', 0))

        df['D%'] = ((df['CMP'] - df['PC']) / df['PC'].replace(0, 1)) * 100
        df['DH%'] = ((df['Day High'] - df['PC']) / df['PC'].replace(0, 1)) * 100
        df['SAlert'] = ((df['CMP'] - df['Day High']) / df['CMP'].replace(0, 1)) * 100

        df['Total Invested'] = df['Quantity'] * df['Average Price']
        df['Current Value'] = df['Quantity'] * df['CMP']
        df['Net P&L'] = df['Current Value'] - df['Total Invested']
        df['ROI (%)'] = (df['Net P&L'] / df['Total Invested'].replace(0, 1)) * 100

        fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(CSV_FILE)), suffix='.csv')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            df.to_csv(f, index=False)
        os.replace(temp_path, CSV_FILE)

        print(f"[{time.strftime('%H:%M:%S')}] Market tick recorded for {len(df)} instruments.")
    except Exception as e:
        print(f"Tick update error: {e}")

    return df

if __name__ == "__main__":
    current_portfolio = None
    print("🚀 Running live market polling engine...")
    try:
        while True:
            current_portfolio = sync_portfolio_registry(current_portfolio)
            current_portfolio = stream_tick_cycle(current_portfolio)
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nProcess halted by user.")