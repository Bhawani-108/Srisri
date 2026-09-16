import os
import threading
import time
import io
import requests
import re
import streamlit as st
import pandas as pd
import backend_updater
from demat_display import build_demat_display_frame, style_demat_table, ORDERED_COLUMNS
from watchlist_display import build_watchlist_display_frame, style_watchlist_table

st.set_page_config(page_title="Live Portfolio & Watchlist", layout="wide")

with open("styles.css", "r", encoding="utf-8") as f:
    css = f.read()
st.markdown(f"", unsafe_allow_html=True)

CSV_FILE = "portfolio.csv"
WATCHLIST_FILE = "watchlist.txt"
BUY_DATE_FILE = "manual_buy_dates.csv"
MOCK_PORTFOLIO_FILE = "mock_portfolio.csv"


# ==========================================
# UNIVERSAL NAMING & SYMBOL MAPPING
# ==========================================
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
def get_all_indexed_symbols():
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
            "segment": segment, "label": label, "search_key": search_key
        })

    exchange_rank = {"NSE": 0, "BSE": 1, "NFO": 2, "MCX": 3, "CDS": 4, "NCO": 5, "BFO": 6}
    segment_rank = {"EQ": 0, "ETF": 1, "MF": 2, "F&O": 3, "COM": 4, "CUR": 5}
    items.sort(key=lambda x: (exchange_rank.get(x["exchange"], 99), segment_rank.get(x["segment"], 99), x["symbol"]))
    return items


# ==========================================
# FILE IO: MANUAL HOLDINGS EDIT
# ==========================================
_CACHED_MANUAL_BUY_DATES = None
_LAST_BUY_DATE_MTIME = 0

def ensure_manual_buy_date_file():
    if not os.path.exists(BUY_DATE_FILE):
        pd.DataFrame(columns=["Stock Name", "Buy Date"]).to_csv(BUY_DATE_FILE, index=False)

def load_manual_buy_dates():
    global _CACHED_MANUAL_BUY_DATES, _LAST_BUY_DATE_MTIME
    ensure_manual_buy_date_file()
    try: current_mtime = os.stat(BUY_DATE_FILE).st_mtime_ns
    except OSError: current_mtime = -1

    if _CACHED_MANUAL_BUY_DATES is not None and current_mtime == _LAST_BUY_DATE_MTIME: return _CACHED_MANUAL_BUY_DATES
    try:
        df = pd.read_csv(BUY_DATE_FILE)
        if df.empty or "Stock Name" not in df.columns or "Buy Date" not in df.columns:
            _CACHED_MANUAL_BUY_DATES = pd.DataFrame(columns=["Stock Name", "Buy Date"])
        else:
            df = df[["Stock Name", "Buy Date"]].copy()
            df["Stock Name"] = df["Stock Name"].astype(str).str.upper()
            df["Buy Date"] = pd.to_datetime(df["Buy Date"], errors="coerce").dt.strftime("%Y-%m-%d")
            _CACHED_MANUAL_BUY_DATES = df.dropna(subset=["Stock Name", "Buy Date"]).reset_index(drop=True)
    except Exception: _CACHED_MANUAL_BUY_DATES = pd.DataFrame(columns=["Stock Name", "Buy Date"])
    
    _LAST_BUY_DATE_MTIME = current_mtime
    return _CACHED_MANUAL_BUY_DATES

def apply_manual_buy_dates(df):
    if df is None or df.empty: return df
    manual_dates = load_manual_buy_dates()
    if manual_dates.empty or "Stock Name" not in df.columns: return df

    df = df.copy()
    mapping = dict(zip(manual_dates["Stock Name"], manual_dates["Buy Date"]))
    mapped_series = df["Stock Name"].map(mapping)
    df["Buy Date"] = mapped_series.where(mapped_series.notna(), df.get("Buy Date"))
    return df


# ==========================================
# FILE IO: WATCHLIST MOCK PORTFOLIO (PAPER TRADING)
# ==========================================
_CACHED_MOCK_PORTFOLIO = None
_LAST_MOCK_MTIME = 0

def ensure_mock_portfolio_file():
    if not os.path.exists(MOCK_PORTFOLIO_FILE):
        pd.DataFrame(columns=["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status"]).to_csv(MOCK_PORTFOLIO_FILE, index=False)

def load_mock_portfolio():
    global _CACHED_MOCK_PORTFOLIO, _LAST_MOCK_MTIME
    ensure_mock_portfolio_file()
    try: current_mtime = os.stat(MOCK_PORTFOLIO_FILE).st_mtime_ns
    except OSError: current_mtime = -1

    if _CACHED_MOCK_PORTFOLIO is not None and current_mtime == _LAST_MOCK_MTIME: return _CACHED_MOCK_PORTFOLIO
    try:
        df = pd.read_csv(MOCK_PORTFOLIO_FILE)
        if df.empty or "Stock Name" not in df.columns:
            _CACHED_MOCK_PORTFOLIO = pd.DataFrame(columns=["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status"])
        else:
            df["Stock Name"] = df["Stock Name"].astype(str).str.upper()
            _CACHED_MOCK_PORTFOLIO = df.reset_index(drop=True)
    except Exception: _CACHED_MOCK_PORTFOLIO = pd.DataFrame(columns=["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status"])
    
    _LAST_MOCK_MTIME = current_mtime
    return _CACHED_MOCK_PORTFOLIO

def apply_mock_portfolio(df):
    if df is None or df.empty: return df
    mock_df = load_mock_portfolio()
    if mock_df.empty or "Stock Name" not in df.columns: return df

    df = df.copy()
    mock_mapping = mock_df.set_index("Stock Name")
    is_watchlist = df["Type"] == "Watchlist"
    
    for col in ["Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status"]:
        if col in mock_df.columns:
            mapped = df["Stock Name"].map(mock_mapping[col])
            if col not in df.columns: df[col] = pd.NA
            df.loc[is_watchlist, col] = mapped[is_watchlist].where(mapped[is_watchlist].notna(), df.loc[is_watchlist, col])
            
    return df


# ==========================================
# START BACKGROUND ENGINE (SINGLETON)
# ==========================================
@st.cache_resource
def start_background_engine():
    def run_backend():
        current_portfolio = None
        while True:
            try:
                current_portfolio = backend_updater.sync_portfolio_registry(current_portfolio)
                current_portfolio = backend_updater.stream_tick_cycle(current_portfolio)
            except Exception as e: print(f"Engine fault: {e}")
            time.sleep(1.5)
    
    engine_thread = threading.Thread(target=run_backend, daemon=True)
    engine_thread.start()
    return engine_thread

start_background_engine()

if not os.path.exists(WATCHLIST_FILE):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f: f.write("")


# ==========================================
# SIDEBAR: ANGEL ONE STYLE SEARCH CONTROLS
# ==========================================
with st.sidebar:
    st.header("⚡ Manage Watchlist")

    search_query = st.text_input(
        "Search", placeholder="Search e.g. tata, reliance, nifty...",
        key="global_search_input", label_visibility="collapsed"
    ).strip().lower()

    ui_category_map = {"All": "All", "Stock": "EQ", "F&O": "F&O", "ETF": "ETF", "Mutual Funds": "MF"}
    selected_pill = st.pills("Filter by segment", options=list(ui_category_map.keys()), default="All", selection_mode="single", label_visibility="collapsed")
    category_filter = ui_category_map.get(selected_pill, "All")

    all_symbols = get_all_indexed_symbols()
    
    if search_query:
        matched_items = [item for item in all_symbols if (category_filter == "All" or item["segment"] == category_filter) and (search_query in item["search_key"])][:60]
    else:
        matched_items = [item for item in all_symbols if (category_filter == "All" or item["segment"] == category_filter)][:60]

    with st.form(key="add_ticker_form", clear_on_submit=True):
        display_options = [item["label"] for item in matched_items]
        selected_label = st.selectbox("Matching Instruments", options=display_options, index=0 if display_options else None, placeholder="Select instrument to add...", label_visibility="collapsed")
        submit_add = st.form_submit_button("➕ Add Ticker", width="stretch")

        if submit_add and selected_label:
            selected_item = next((item for item in matched_items if item["label"] == selected_label), None)
            if selected_item is None: st.warning("Please choose a valid symbol.")
            else:
                new_ticker = str(selected_item["unique_key"]).strip().upper()
                with open(WATCHLIST_FILE, "r", encoding="utf-8") as f: existing = [line.strip().upper() for line in f if line.strip()]

                if new_ticker in existing: st.warning(f"'{new_ticker}' is already on the watchlist.")
                else:
                    with open(WATCHLIST_FILE, "a", encoding="utf-8") as f: f.write(f"{new_ticker}\n")
                    st.success(f"Added '{new_ticker}'. Syncing live feed...")
                    time.sleep(0.5)
                    st.rerun()

    st.divider()

    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            active_watchlist = [line.strip().upper() for line in f if line.strip()]
        if active_watchlist:
            st.subheader("Remove Ticker")
            stock_to_remove = st.selectbox("Select ticker to remove", active_watchlist)
            if st.button("🗑️ Delete from Watchlist", width="stretch"):
                updated_list = [s for s in active_watchlist if s != stock_to_remove]
                with open(WATCHLIST_FILE, "w", encoding="utf-8") as f: f.write("\n".join(updated_list) + ("\n" if updated_list else ""))
                st.success(f"Removed '{stock_to_remove}'")
                time.sleep(0.5)
                st.rerun()


# ==========================================
# DASHBOARD DISPLAY & LIVE REFRESH FRAGMENTS
# ==========================================
def ensure_backend_data_loaded():
    try:
        if not os.path.exists(CSV_FILE):
            with open(CSV_FILE, "w", encoding="utf-8") as f: f.write("")
        if os.path.getsize(CSV_FILE) == 0:
            current_portfolio = backend_updater.sync_portfolio_registry(None)
            current_portfolio = backend_updater.stream_tick_cycle(current_portfolio)
    except Exception as e: print(f"Preflight sync warning: {e}")

def load_portfolio_snapshot(path: str = CSV_FILE):
    try:
        if not os.path.exists(path): return pd.DataFrame()
        frame = pd.read_csv(path)
        return frame if not frame.empty else pd.DataFrame()
    except Exception: return pd.DataFrame()

def get_clean_data():
    df = load_portfolio_snapshot(CSV_FILE)
    df = apply_manual_buy_dates(df)
    df = apply_mock_portfolio(df)
    
    if not df.empty and 'CMP' in df.columns:
        df['Quantity'] = pd.to_numeric(df.get('Quantity', 0), errors='coerce').fillna(0)
        buy_price_series = pd.to_numeric(df.get('Buy Price', pd.NA), errors='coerce')
        avg_price_series = pd.to_numeric(df.get('Average Price', 0), errors='coerce')
        df['Effective_Buy_Price'] = buy_price_series.combine_first(avg_price_series).fillna(0)
        df['CMP'] = pd.to_numeric(df['CMP'], errors='coerce').fillna(0)
        
        if 'Sell Price' not in df.columns: df['Sell Price'] = pd.NA
        if 'Side' not in df.columns: df['Side'] = 'LONG'
        if 'Status' not in df.columns: df['Status'] = 'OPEN'
        
        df['Total Invested'] = df['Quantity'] * df['Effective_Buy_Price']
        
        is_wl = df['Type'] == 'Watchlist'
        df['Current Value'] = df['Total Invested'] 
        
        for idx in df[is_wl].index:
            q = df.loc[idx, 'Quantity']
            bp = df.loc[idx, 'Effective_Buy_Price']
            sp = pd.to_numeric(df.loc[idx, 'Sell Price'], errors='coerce')
            c = df.loc[idx, 'CMP']
            s = str(df.loc[idx, 'Side']).upper()
            st_val = str(df.loc[idx, 'Status']).upper()
            
            if st_val == "CLOSED" and not pd.isna(sp):
                df.loc[idx, 'Current Value'] = sp * q
                if s == "SHORT":
                    df.loc[idx, 'Net P&L'] = (bp - sp) * q
                else:
                    df.loc[idx, 'Net P&L'] = (sp - bp) * q
            else:
                df.loc[idx, 'Current Value'] = c * q
                if s == "SHORT":
                    df.loc[idx, 'Net P&L'] = (bp - c) * q
                else:
                    df.loc[idx, 'Net P&L'] = (c - bp) * q
                    
        is_hold = df['Type'] == 'Holding'
        df.loc[is_hold, 'Current Value'] = df.loc[is_hold, 'Quantity'] * df.loc[is_hold, 'CMP']
        df.loc[is_hold, 'Net P&L'] = df.loc[is_hold, 'Current Value'] - df.loc[is_hold, 'Total Invested']
        
    return df

ensure_backend_data_loaded()

st.title("📊 Live Portfolio & Market Watchlist")
st.caption("Live feed via Angel One SmartAPI • Streaming updates every 2s")

# ------------------------------------------
# VIEW SELECTOR (MOVED TO TOP)
# ------------------------------------------
view_mode = st.pills(
    "Select Table View",
    options=["💼 Demat Holdings", "👀 Market Watchlist"],
    default="💼 Demat Holdings",
    key="view_mode_pills",
    label_visibility="collapsed"
)
st.divider()

# ------------------------------------------
# CONTEXTUAL DATA EDITORS (DEPENDS ON VIEW)
# ------------------------------------------
if view_mode == "💼 Demat Holdings":
    with st.expander("✏️ Edit Manual Buy Dates (Holdings)", expanded=False):
        init_df = get_clean_data()
        init_holdings = init_df[init_df['Type'] == 'Holding'].copy() if not init_df.empty and 'Type' in init_df.columns else pd.DataFrame()
        
        if not init_holdings.empty:
            disp_holdings_static = build_demat_display_frame(init_holdings)
            buy_date_editor = disp_holdings_static[["Stock Name", "Buy Date"]].copy()
            
            edited_buy_dates = st.data_editor(buy_date_editor, width="stretch", hide_index=True, disabled=["Stock Name"], key="holdings_editor")
            
            if not edited_buy_dates.equals(buy_date_editor):
                manual_rows = edited_buy_dates.copy()
                manual_rows["Stock Name"] = manual_rows["Stock Name"].astype(str).str.upper()
                manual_rows["Buy Date"] = pd.to_datetime(manual_rows["Buy Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                manual_rows = manual_rows.dropna(subset=["Buy Date"]).drop_duplicates(subset=["Stock Name"], keep="last")

                if not manual_rows.empty:
                    ledger = load_manual_buy_dates()
                    ledger = ledger[~ledger["Stock Name"].isin(manual_rows["Stock Name"])].copy()
                    ledger = pd.concat([ledger, manual_rows[["Stock Name", "Buy Date"]]], ignore_index=True)
                    ledger.to_csv(BUY_DATE_FILE, index=False)
                    st.rerun()
        else:
            st.info("No holdings available to edit.")

else:
    with st.expander("📝 Edit Mock Portfolio (Paper Trading)", expanded=False):
        init_df = get_clean_data()
        init_watchlist = init_df[init_df['Type'] == 'Watchlist'].copy() if not init_df.empty and 'Type' in init_df.columns else pd.DataFrame()
        
        if not init_watchlist.empty:
            for col, default_val in [("Buy Date", None), ("Quantity", 100), ("Buy Price", 0.0), 
                                     ("Sell Price", 0.0), ("Side", "LONG"), ("Status", "OPEN")]:
                if col not in init_watchlist.columns: 
                    init_watchlist[col] = default_val
                    
            mock_editor_df = init_watchlist[["Stock Name", "Side", "Status", "Quantity", "Buy Price", "Sell Price", "Buy Date"]].copy()
            
            edited_mock = st.data_editor(
                mock_editor_df, 
                width="stretch", 
                hide_index=True, 
                disabled=["Stock Name"], 
                column_config={
                    "Side": st.column_config.SelectboxColumn("Side", options=["LONG", "SHORT"], required=True),
                    "Status": st.column_config.SelectboxColumn("Status", options=["OPEN", "CLOSED"], required=True),
                },
                key="mock_editor"
            )
            
            if not edited_mock.equals(mock_editor_df):
                manual_rows = edited_mock.copy()
                manual_rows["Stock Name"] = manual_rows["Stock Name"].astype(str).str.upper()
                if "Buy Date" in manual_rows.columns: 
                    manual_rows["Buy Date"] = pd.to_datetime(manual_rows["Buy Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                
                ledger = load_mock_portfolio()
                ledger = ledger[~ledger["Stock Name"].isin(manual_rows["Stock Name"])].copy()
                ledger = pd.concat([ledger, manual_rows], ignore_index=True)
                ledger.to_csv(MOCK_PORTFOLIO_FILE, index=False)
                st.rerun()
        else:
            st.info("Watchlist is empty. Use the sidebar to add stocks to paper trade.")

st.divider()

# ------------------------------------------
# SINGLE OPTIMIZED LIVE FRAGMENT
# ------------------------------------------
@st.fragment(run_every="2s")
def live_dashboard():
    df = get_clean_data()
    if df.empty or 'Type' not in df.columns:
        st.info("Syncing backend data stream...")
        return

    holdings_df = df[df['Type'] == 'Holding'].copy().sort_values(by="Stock Name")
    watchlist_df = df[df['Type'] == 'Watchlist'].copy().sort_values(by="Stock Name")

    active_df = holdings_df if view_mode == "💼 Demat Holdings" else watchlist_df

    total_invested = active_df['Total Invested'].sum() if not active_df.empty and 'Total Invested' in active_df.columns else 0.0
    current_value = active_df['Current Value'].sum() if not active_df.empty and 'Current Value' in active_df.columns else 0.0
    total_pnl = active_df['Net P&L'].sum() if not active_df.empty and 'Net P&L' in active_df.columns else 0.0
    portfolio_roi = (total_pnl / total_invested if total_invested > 0 else 0) * 100

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Portfolio Value", f"₹{current_value:,.2f}")
    col2.metric("Total Invested", f"₹{total_invested:,.2f}")
    col3.metric("Net Profit / Loss", f"₹{total_pnl:,.2f}", delta=f"₹{total_pnl:,.2f}")
    col4.metric("Total ROI", f"{portfolio_roi:.2f}%", delta=f"{portfolio_roi:.2f}%")
    st.divider()

    if view_mode == "💼 Demat Holdings":
        if not holdings_df.empty:
            disp_holdings = build_demat_display_frame(holdings_df)
            styled_holdings = style_demat_table(disp_holdings)
            st.dataframe(styled_holdings, width="stretch", hide_index=True)
        else:
            st.info("No delivery holdings currently in your Angel One account.")
    else:
        if not watchlist_df.empty:
            disp_watchlist = build_watchlist_display_frame(watchlist_df)
            styled_watchlist = style_watchlist_table(disp_watchlist)
            st.dataframe(styled_watchlist, width="stretch", hide_index=True, height=500)
        else:
            st.info("Watchlist is empty. Use the sidebar on the left to add tickers.")

live_dashboard()