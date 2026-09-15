import os
import threading
import time
import streamlit as st
import pandas as pd
import plotly.express as px
import backend_updater
from portfolio_display import build_ordered_display_frame, ORDERED_COLUMNS

st.set_page_config(page_title="Live Portfolio & Watchlist", layout="wide")

with open("styles.css", "r", encoding="utf-8") as f:
    css = f.read()

st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

CSV_FILE = "portfolio.csv"
WATCHLIST_FILE = "watchlist.txt"
BUY_DATE_FILE = "manual_buy_dates.csv"


@st.cache_data(ttl=3600)
def get_searchable_symbol_options():
    master = getattr(backend_updater, "TOKEN_MAP", {}) or {}
    exchange_map = getattr(backend_updater, "EXCHANGE_MAP", {}) or {}
    segment_map = getattr(backend_updater, "SEGMENT_MAP", {}) or {}

    options = []
    seen = set()
    for symbol, token in (master or {}).items():
        clean = str(symbol).strip().upper()
        if not clean or not str(token).strip():
            continue
        exchange = str(exchange_map.get(clean, "NSE")).upper()
        segment = str(segment_map.get(clean, backend_updater.infer_symbol_segment(clean, exchange))).upper()
        label = f"[{exchange}] {clean} • {segment}"
        if clean not in seen:
            seen.add(clean)
            options.append({"symbol": clean, "exchange": exchange, "segment": segment, "label": label})

    for symbol, exchange in (exchange_map or {}).items():
        clean = str(symbol).strip().upper()
        if not clean or clean in seen:
            continue
        exchange_name = str(exchange).upper()
        segment = str(backend_updater.infer_symbol_segment(clean, exchange_name)).upper()
        options.append({"symbol": clean, "exchange": exchange_name, "segment": segment, "label": f"[{exchange_name}] {clean} • {segment}"})

    exchange_rank = {"NSE": 0, "BSE": 1, "NFO": 2, "MCX": 3}
    segment_rank = {"EQ": 0, "ETF": 1, "MF": 2, "F&O": 3, "COM": 4, "CUR": 5}
    options.sort(key=lambda x: (exchange_rank.get(x["exchange"], 99), segment_rank.get(x["segment"], 99), x["symbol"]))
    return options


def ensure_manual_buy_date_file():
    if not os.path.exists(BUY_DATE_FILE):
        pd.DataFrame(columns=["Stock Name", "Buy Date"]).to_csv(BUY_DATE_FILE, index=False)


def load_manual_buy_dates():
    ensure_manual_buy_date_file()
    try:
        df = pd.read_csv(BUY_DATE_FILE)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=["Stock Name", "Buy Date"])

    if df.empty:
        return pd.DataFrame(columns=["Stock Name", "Buy Date"])

    if "Stock Name" not in df.columns or "Buy Date" not in df.columns:
        return pd.DataFrame(columns=["Stock Name", "Buy Date"])

    df = df[["Stock Name", "Buy Date"]].copy()
    df["Stock Name"] = df["Stock Name"].astype(str).str.upper()
    df["Buy Date"] = pd.to_datetime(df["Buy Date"], errors="coerce")
    df = df.dropna(subset=["Stock Name", "Buy Date"]).copy()
    df["Buy Date"] = df["Buy Date"].dt.strftime("%Y-%m-%d")
    return df.reset_index(drop=True)


def apply_manual_buy_dates(df):
    if df is None or df.empty:
        return df

    df = df.copy()
    manual_dates = load_manual_buy_dates()
    if manual_dates.empty:
        return df

    mapping = dict(zip(manual_dates["Stock Name"], manual_dates["Buy Date"]))
    if "Stock Name" in df.columns:
        df["Buy Date"] = df["Stock Name"].map(mapping).where(pd.notna(df["Stock Name"].map(mapping)), df.get("Buy Date"))

    return df


def style_live_delta_columns(df, columns=("D%", "% Profit")):
    if df is None or df.empty:
        return df

    work_df = df.copy()
    target_columns = [col for col in columns if col in work_df.columns]
    if not target_columns:
        return work_df

    for col in target_columns:
        work_df[col] = pd.to_numeric(work_df[col], errors="coerce")

    def highlight_value(value):
        if pd.isna(value):
            return "background-color: #f3f4f6; color: #111827;"
        if value > 0:
            return "background-color: #1f9d55; color: white;"
        if value < 0:
            return "background-color: #d64545; color: white;"
        return "background-color: #e5e7eb; color: #111827;"

    def row_style(row):
        styles = ["" for _ in row]
        for idx, col_name in enumerate(row.index):
            if col_name in target_columns:
                styles[idx] = highlight_value(row[col_name])
        return styles

    return work_df.style.apply(row_style, axis=1)

# ==========================================
# START BACKGROUND ENGINE ON STREAMLIT CLOUD
# ==========================================
@st.cache_resource
def start_background_engine():
    def run_backend():
        current_portfolio = None
        while True:
            try:
                current_portfolio = backend_updater.sync_portfolio_registry(current_portfolio)
                current_portfolio = backend_updater.stream_tick_cycle(current_portfolio)
            except Exception as e:
                print(f"Engine fault: {e}")
            time.sleep(3)
    
    engine_thread = threading.Thread(target=run_backend, daemon=True)
    engine_thread.start()
    return engine_thread

# Initialize the engine (runs once per server instance)
start_background_engine()

if not os.path.exists(WATCHLIST_FILE):
    with open(WATCHLIST_FILE, "w") as f:
        f.write("")

# ==========================================
# SIDEBAR: LIVE WATCHLIST CONTROLS
# ==========================================
with st.sidebar:
    st.header("⚡ Manage Watchlist")

    category_options = ["All", "EQ", "ETF", "MF", "F&O", "COM", "CUR"]
    category_filter = st.pills(
        "",
        options=category_options,
        default="All",
        selection_mode="single",
        label_visibility="collapsed",
    )

    with st.form(key="add_ticker_form", clear_on_submit=True):
        symbol_options = get_searchable_symbol_options()
        filtered_options = [item for item in symbol_options if category_filter == "All" or item["segment"] == category_filter]
        display_options = [item["label"] for item in filtered_options]

        selected_label = st.selectbox(
            "Search stock",
            options=display_options,
            index=None,
            placeholder="Type to search any stock...",
            help="Results are grouped by exchange and instrument type, e.g. [NSE] RELIANCE • EQ"
        )
        submit_add = st.form_submit_button("➕ Add Ticker", width="stretch")

        if submit_add and selected_label:
            selected_item = next((item for item in filtered_options if item["label"] == selected_label), None)
            if selected_item is None:
                st.warning("Please choose a valid symbol from the filtered list.")
            else:
                new_ticker = str(selected_item["symbol"]).strip().upper()
                with open(WATCHLIST_FILE, "r") as f:
                    existing = [line.strip().upper() for line in f if line.strip()]

                if new_ticker in existing:
                    st.warning(f"'{new_ticker}' is already on the watchlist.")
                else:
                    with open(WATCHLIST_FILE, "a") as f:
                        f.write(f"{new_ticker}\n")
                    st.success(f"Added '{new_ticker}'. Syncing live feed...")

    st.divider()

    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r") as f:
            active_watchlist = [line.strip().upper() for line in f if line.strip()]
        
        if active_watchlist:
            st.subheader("Remove Ticker")
            stock_to_remove = st.selectbox("Select ticker to remove", active_watchlist)
            if st.button("🗑️ Delete from Watchlist", width="stretch"):
                updated_list = [s for s in active_watchlist if s != stock_to_remove]
                with open(WATCHLIST_FILE, "w") as f:
                    f.write("\n".join(updated_list) + ("\n" if updated_list else ""))
                st.success(f"Removed '{stock_to_remove}'")
                st.rerun()

    st.divider()
    st.caption("Double-click a holding's Buy Date cell in the table below to edit it and save it automatically.")

# ==========================================
# DASHBOARD DISPLAY & LIVE REFRESH FRAGMENT
# ==========================================
def ensure_backend_data_loaded():
    try:
        if not os.path.exists(CSV_FILE):
            with open(CSV_FILE, "w", encoding="utf-8") as f:
                f.write("")

        if os.path.getsize(CSV_FILE) == 0:
            current_portfolio = backend_updater.sync_portfolio_registry(None)
            current_portfolio = backend_updater.stream_tick_cycle(current_portfolio)
    except Exception as e:
        print(f"Preflight sync warning: {e}")

ensure_backend_data_loaded()

st.title("📊 Live Portfolio & Market Watchlist")
st.caption("Live feed via Angel One SmartAPI • Streaming updates every 2s")
st.divider()

with st.container():
    chart_toggle = st.toggle("📈 Show Charts", value=False, key="show_chart_panel")

@st.fragment(run_every="2s")
def live_dashboard_matrix():
    try:
        ensure_backend_data_loaded()
        df = pd.read_csv(CSV_FILE)
        df = apply_manual_buy_dates(df)

        if 'Type' not in df.columns:
            st.info("Syncing backend data stream...")
            return

        holdings_df = df[df['Type'] == 'Holding'].copy()
        watchlist_df = df[df['Type'] == 'Watchlist'].copy()

        total_invested = holdings_df['Total Invested'].sum() if not holdings_df.empty else 0.0
        current_value = holdings_df['Current Value'].sum() if not holdings_df.empty else 0.0
        total_pnl = holdings_df['Net P&L'].sum() if not holdings_df.empty else 0.0
        portfolio_roi = (total_pnl / total_invested if total_invested > 0 else 0) * 100

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Portfolio Value", f"₹{current_value:,.2f}")
        col2.metric("Total Invested", f"₹{total_invested:,.2f}")
        col3.metric("Net Profit / Loss", f"₹{total_pnl:,.2f}", delta=f"₹{total_pnl:,.2f}")
        col4.metric("Total ROI", f"{portfolio_roi:.2f}%", delta=f"{portfolio_roi:.2f}%")
        st.divider()

        if chart_toggle:
            left_col, right_col = st.columns(2)
            with left_col:
                st.subheader("📁 Portfolio Allocation")
                if not holdings_df.empty and current_value > 0:
                    fig_pie = px.pie(
                        holdings_df, values='Current Value', names='Stock Name', hole=0.4,
                        color_discrete_sequence=px.colors.qualitative.Safe
                    )
                    fig_pie.update_traces(textinfo='percent+label')
                    st.plotly_chart(fig_pie, width="stretch")
                else:
                    st.info("No active demat holdings found.")

            with right_col:
                st.subheader("📊 Profit & Loss by Holding (₹)")
                if not holdings_df.empty and current_value > 0:
                    fig_bar = px.bar(
                        holdings_df, x='Stock Name', y='Net P&L', color='Net P&L',
                        color_continuous_scale=['#FF4B4B', '#00CC96']
                    )
                    st.plotly_chart(fig_bar, width="stretch")
                else:
                    st.info("No active demat holdings found.")

            st.divider()

        tab1, tab2 = st.tabs(["💼 Demat Holdings", "👀 Market Watchlist"])

        with tab1:
            if not holdings_df.empty:
                disp_holdings = build_ordered_display_frame(holdings_df, "Holding")
                disp_holdings = disp_holdings.reindex(columns=ORDERED_COLUMNS)

                buy_date_editor = disp_holdings[["Stock Name", "Buy Date"]].copy()
                edited_buy_dates = st.data_editor(
                    buy_date_editor,
                    width="stretch",
                    hide_index=True,
                    disabled=["Stock Name"],
                    key="holdings_buy_date_editor",
                )

                if not edited_buy_dates.equals(buy_date_editor):
                    manual_rows = edited_buy_dates[["Stock Name", "Buy Date"]].copy()
                    manual_rows = manual_rows[manual_rows["Stock Name"].notna()].copy()
                    manual_rows["Stock Name"] = manual_rows["Stock Name"].astype(str).str.upper()
                    manual_rows["Buy Date"] = pd.to_datetime(manual_rows["Buy Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                    manual_rows = manual_rows.dropna(subset=["Buy Date"]).drop_duplicates(subset=["Stock Name"], keep="last")

                    if not manual_rows.empty:
                        ledger = load_manual_buy_dates()
                        ledger = ledger[~ledger["Stock Name"].isin(manual_rows["Stock Name"])].copy()
                        ledger = pd.concat([ledger, manual_rows[["Stock Name", "Buy Date"]]], ignore_index=True)
                        ledger.to_csv(BUY_DATE_FILE, index=False)

                styled_holdings = style_live_delta_columns(disp_holdings, ("D%", "% Profit"))
                st.dataframe(styled_holdings, width="stretch", hide_index=True)
            else:
                st.info("No delivery holdings currently in your Angel One account.")

        with tab2:
            if not watchlist_df.empty:
                disp_watchlist = build_ordered_display_frame(watchlist_df, "Watchlist")
                disp_watchlist = disp_watchlist.reindex(columns=ORDERED_COLUMNS)
                styled_watchlist = style_live_delta_columns(disp_watchlist, ("D%", "% Profit"))
                st.dataframe(styled_watchlist, width="stretch", hide_index=True, height=500)
            else:
                st.info("Watchlist is empty. Use the sidebar on the left to add tickers.")

    except (pd.errors.EmptyDataError, FileNotFoundError):
        st.info("Booting data engine and syncing live broker stream...")
    except Exception as e:
        st.error(f"Dashboard notice: {e}")

live_dashboard_matrix()