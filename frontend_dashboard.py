import time
import threading
import os
import streamlit as st
import pandas as pd
from streamlit.runtime.scriptrunner import add_script_run_ctx

import backend_updater
from brokers.config import get_active_broker_name, get_supported_brokers
from core.formula_engine import get_configured_column_order
from core.storage_manager import load_column_prefs, save_column_prefs
from core.portfolio_service import ensure_backend_data_loaded, get_clean_data
from core.symbol_catalog import get_all_indexed_symbols
from components.sidebar import render_sidebar
from components.mock_editor import render_mock_portfolio_editor
from views.demat_display import build_demat_display_frame, style_demat_table
from views.watchlist_display import build_watchlist_display_frame, style_watchlist_table

st.set_page_config(page_title="Live Portfolio & Watchlist", layout="wide")

if os.path.exists("styles.css"):
    with open("styles.css", "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Broker Switcher State Management (Initialized before background daemon starts)
if "active_broker" not in st.session_state:
    st.session_state.active_broker = get_active_broker_name()

# Stamp environment variable immediately on initial boot for all daemon threads
os.environ["ACTIVE_BROKER"] = str(st.session_state.active_broker).strip().lower()

# Start background updater singleton
@st.cache_resource
def start_background_engine():
    def run_backend():
        current_portfolio = None
        while True:
            try:
                current_portfolio = backend_updater.sync_portfolio_registry(current_portfolio)
                current_portfolio = backend_updater.stream_tick_cycle(current_portfolio)
            except Exception:
                pass
            time.sleep(1.0)
    
    t = threading.Thread(target=run_backend, daemon=True)
    add_script_run_ctx(t)
    t.start()
    return t

broker_options = get_supported_brokers()
selected_broker = st.selectbox(
    "Select Active Broker Integration",
    options=broker_options,
    index=broker_options.index(st.session_state.active_broker) if st.session_state.active_broker in broker_options else 0,
    key="active_broker_select",
)

if selected_broker != st.session_state.active_broker:
    st.session_state.active_broker = selected_broker
    os.environ["ACTIVE_BROKER"] = str(selected_broker).strip().lower()
    backend_updater.reset_runtime_state()
    get_all_indexed_symbols.clear()
    with st.spinner(f"Switching feed to {selected_broker}..."):
        fresh_df = backend_updater.sync_portfolio_registry(None)
        if fresh_df is not None and not fresh_df.empty:
            backend_updater.stream_tick_cycle(fresh_df)
    st.rerun()

start_background_engine()
ensure_backend_data_loaded()

# Sidebar
render_sidebar()

# Main Header
st.title("📊 Live Portfolio & Market Watchlist")
broker_label = (
    "Angel One SmartAPI"
    if selected_broker == "angel_one"
    else ("IndMoney API" if selected_broker == "indmoney" else "US Stocks")
)
st.caption(f"Live feed via {broker_label} • Streaming updates every 1s")

view_mode = st.pills(
    "Select Table View",
    options=["💼 Demat Holdings", "👀 Market Watchlist"],
    default="💼 Demat Holdings",
    key="view_mode_pills",
    label_visibility="collapsed",
)
st.divider()

# Column Visibility Manager
def update_centralized_prefs():
    ordered_cols = get_configured_column_order()
    new_visible = [col for col in ordered_cols if st.session_state.get(f"cent_col_chk_{col}", True)]
    save_column_prefs(new_visible)

with st.expander("👁️ Column Visibility Manager", expanded=False):
    st.caption("Toggle columns on or off:")
    configured_order = get_configured_column_order()
    current_visible_cols = set(load_column_prefs())
    cols_grid = st.columns(4)
    for idx, col in enumerate(configured_order):
        with cols_grid[idx % 4]:
            st.checkbox(
                col, 
                value=col in current_visible_cols, 
                key=f"cent_col_chk_{col}",
                on_change=update_centralized_prefs,
            )

st.divider()

render_mock_portfolio_editor()

st.divider()

# Live Table & Metric Stream Fragment
@st.fragment(run_every="1s")
def live_dashboard():
    df = get_clean_data()
    if df.empty or "Type" not in df.columns:
        st.info("Syncing backend data stream...")
        return

    # Filter rows matching active selected broker
    active_broker_name = st.session_state.active_broker.lower()
    if "Broker" in df.columns:
        df = df[df["Broker"].str.lower() == active_broker_name].copy()

    holdings_df = df[df["Type"] == "Holding"].copy().sort_values(by="Stock Name")
    watchlist_df = df[df["Type"] == "Watchlist"].copy().sort_values(by="Stock Name")

    is_watchlist_view = "Watchlist" in str(view_mode)
    active_df = watchlist_df if is_watchlist_view else holdings_df

    total_invested = active_df["Total Invested"].sum() if not active_df.empty and "Total Invested" in active_df.columns else 0.0
    current_value = active_df["Current Value"].sum() if not active_df.empty and "Current Value" in active_df.columns else 0.0
    total_pnl = active_df["Net P&L"].sum() if not active_df.empty and "Net P&L" in active_df.columns else 0.0
    portfolio_roi = (total_pnl / total_invested if total_invested > 0 else 0.0) * 100

    day_change_pct = 0.0
    day_pnl = 0.0
    if not active_df.empty and "CMP" in active_df.columns and "PC" in active_df.columns and "Quantity" in active_df.columns:
        valid_mask = (active_df["Quantity"] > 0) & (active_df["PC"] > 0) & (active_df["CMP"] > 0)
        if valid_mask.any():
            prev_close_val = (active_df.loc[valid_mask, "Quantity"] * active_df.loc[valid_mask, "PC"]).sum()
            curr_val_active = (active_df.loc[valid_mask, "Quantity"] * active_df.loc[valid_mask, "CMP"]).sum()
            day_pnl = curr_val_active - prev_close_val
            if prev_close_val > 0:
                day_change_pct = (day_pnl / prev_close_val) * 100
        elif is_watchlist_view and "D%" in active_df.columns:
            day_change_pct = pd.to_numeric(active_df["D%"], errors="coerce").dropna().mean() or 0.0

    pnl_prefix = "-₹" if total_pnl < 0 else "₹"
    pnl_display = f"{pnl_prefix}{abs(total_pnl):,.2f}"
    pnl_delta = f"-₹{abs(total_pnl):,.2f}" if total_pnl < 0 else f"+₹{abs(total_pnl):,.2f}"

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Portfolio Value", f"₹{current_value:,.2f}")
    col2.metric("Total Invested", f"₹{total_invested:,.2f}")
    col3.metric("Net Profit / Loss", pnl_display, delta=pnl_delta)
    col4.metric("Total ROI", f"{portfolio_roi:.2f}%", delta=f"{portfolio_roi:.2f}%")

    if is_watchlist_view and total_invested == 0:
        col5.metric("1D % Change (Avg)", f"{day_change_pct:+.2f}%", delta=f"{day_change_pct:+.2f}%")
    else:
        day_delta_str = f"-₹{abs(day_pnl):,.2f}" if day_pnl < 0 else f"+₹{abs(day_pnl):,.2f}"
        col5.metric("1D % Change", f"{day_change_pct:+.2f}%", delta=day_delta_str)

    st.divider()

    visible_cols_set = set(load_column_prefs())
    active_order = get_configured_column_order()

    if not is_watchlist_view:
        if not holdings_df.empty:
            disp = build_demat_display_frame(holdings_df)
            active_cols = [c for c in active_order if c in visible_cols_set and c in disp.columns]
            st.dataframe(style_demat_table(disp[active_cols]), column_order=active_cols, width="stretch", hide_index=True)
        else:
            st.info(f"No delivery holdings currently found for {broker_label}.")
    else:
        if not watchlist_df.empty:
            disp = build_watchlist_display_frame(watchlist_df)
            active_cols = [c for c in active_order if c in visible_cols_set and c in disp.columns]
            st.dataframe(style_watchlist_table(disp[active_cols]), column_order=active_cols, width="stretch", hide_index=True, height=500)
        else:
            st.info("Watchlist is empty. Use the sidebar on the left to add tickers.")

live_dashboard()