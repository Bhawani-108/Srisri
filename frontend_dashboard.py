import os
import threading
import time
import streamlit as st
import pandas as pd
import plotly.express as px
import backend_updater
from portfolio_display import build_ordered_display_frame, ORDERED_COLUMNS

st.set_page_config(page_title="Live Portfolio & Watchlist", layout="wide")

CSV_FILE = "portfolio.csv"
WATCHLIST_FILE = "watchlist.txt"
BUY_DATE_FILE = "manual_buy_dates.csv"


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
    
    with st.form(key="add_ticker_form", clear_on_submit=True):
        new_ticker = st.text_input("Enter NSE Ticker", placeholder="e.g. INFY, TATAPOWER").strip().upper()
        submit_add = st.form_submit_button("➕ Add Ticker", width="stretch")
        
        if submit_add and new_ticker:
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
                edited_holdings = st.data_editor(
                    disp_holdings,
                    width="stretch",
                    hide_index=True,
                    disabled=[c for c in disp_holdings.columns if c != "Buy Date"],
                    use_container_width=True,
                    key="holdings_table_editor",
                )

                if not edited_holdings.equals(disp_holdings):
                    manual_rows = edited_holdings[["Stock Name", "Buy Date"]].copy()
                    manual_rows = manual_rows[manual_rows["Stock Name"].notna()].copy()
                    manual_rows["Stock Name"] = manual_rows["Stock Name"].astype(str).str.upper()
                    manual_rows["Buy Date"] = pd.to_datetime(manual_rows["Buy Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                    manual_rows = manual_rows.dropna(subset=["Buy Date"]).drop_duplicates(subset=["Stock Name"], keep="last")

                    if not manual_rows.empty:
                        ledger = load_manual_buy_dates()
                        ledger = ledger[~ledger["Stock Name"].isin(manual_rows["Stock Name"])].copy()
                        ledger = pd.concat([ledger, manual_rows[["Stock Name", "Buy Date"]]], ignore_index=True)
                        ledger.to_csv(BUY_DATE_FILE, index=False)

                st.dataframe(edited_holdings, width="stretch", height=500)
            else:
                st.info("No delivery holdings currently in your Angel One account.")

        with tab2:
            if not watchlist_df.empty:
                disp_watchlist = build_ordered_display_frame(watchlist_df, "Watchlist")
                disp_watchlist = disp_watchlist.reindex(columns=ORDERED_COLUMNS)
                st.dataframe(disp_watchlist, width="stretch", height=500)
            else:
                st.info("Watchlist is empty. Use the sidebar on the left to add tickers.")

    except (pd.errors.EmptyDataError, FileNotFoundError):
        st.info("Booting data engine and syncing live broker stream...")
    except Exception as e:
        st.error(f"Dashboard notice: {e}")

live_dashboard_matrix()