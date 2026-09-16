import time
import threading
import streamlit as st
import pandas as pd
from streamlit.runtime.scriptrunner import add_script_run_ctx

import backend_updater
from core.storage_manager import load_column_prefs, save_column_prefs
from core.portfolio_service import ensure_backend_data_loaded, get_clean_data
from components.sidebar import render_sidebar
from components.mock_editor import render_mock_portfolio_editor
from views.demat_display import build_demat_display_frame, style_demat_table, ORDERED_COLUMNS
from views.watchlist_display import build_watchlist_display_frame, style_watchlist_table

st.set_page_config(page_title="Live Portfolio & Watchlist", layout="wide")

with open("styles.css", "r", encoding="utf-8") as f:
    st.markdown(f"", unsafe_allow_html=True)

# Start background updater singleton with active context
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
            time.sleep(1.5)
    
    t = threading.Thread(target=run_backend, daemon=True)
    add_script_run_ctx(t)
    t.start()
    return t

start_background_engine()
ensure_backend_data_loaded()

# Sidebar
render_sidebar()

# Main Title & View Toggle
st.title("📊 Live Portfolio & Market Watchlist")
st.caption("Live feed via Angel One SmartAPI • Streaming updates every 2s")

view_mode = st.pills(
    "Select Table View",
    options=["💼 Demat Holdings", "👀 Market Watchlist"],
    default="💼 Demat Holdings",
    key="view_mode_pills",
    label_visibility="collapsed"
)
st.divider()

# Centralized Column Visibility Manager
def update_centralized_prefs():
    new_visible = [col for col in ORDERED_COLUMNS if st.session_state.get(f"cent_col_chk_{col}", True)]
    save_column_prefs(new_visible)

with st.expander("👁️ Column Visibility Manager", expanded=False):
    st.caption("Toggle columns on or off. Preferences apply globally to both Demat and Watchlist views:")
    current_visible_cols = load_column_prefs()
    cols_grid = st.columns(4)
    for idx, col in enumerate(ORDERED_COLUMNS):
        with cols_grid[idx % 4]:
            st.checkbox(
                col, 
                value=col in current_visible_cols, 
                key=f"cent_col_chk_{col}",
                on_change=update_centralized_prefs
            )

st.divider()

# Single unified positions & paper trading editor (covers buy dates, paper trades, prices)
render_mock_portfolio_editor()

st.divider()

# Live Table & Metric Stream Fragment
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

    day_change_pct = 0.0
    day_pnl = 0.0
    if not active_df.empty and 'CMP' in active_df.columns and 'PC' in active_df.columns and 'Quantity' in active_df.columns:
        prev_close_val = (active_df['Quantity'] * active_df['PC']).sum()
        curr_val_active = (active_df['Quantity'] * active_df['CMP']).sum()
        day_pnl = curr_val_active - prev_close_val
        if prev_close_val > 0:
            day_change_pct = (day_pnl / prev_close_val) * 100
        elif view_mode == "👀 Market Watchlist" and 'D%' in active_df.columns:
            day_change_pct = pd.to_numeric(active_df['D%'], errors='coerce').dropna().mean() or 0.0

    # Ensure negative values start with '-' so Streamlit displays red with a down-arrow
    pnl_prefix = "-₹" if total_pnl < 0 else "₹"
    pnl_display = f"{pnl_prefix}{abs(total_pnl):,.2f}"
    pnl_delta = f"-₹{abs(total_pnl):,.2f}" if total_pnl < 0 else f"+₹{abs(total_pnl):,.2f}"

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Portfolio Value", f"₹{current_value:,.2f}")
    col2.metric("Total Invested", f"₹{total_invested:,.2f}")
    col3.metric("Net Profit / Loss", pnl_display, delta=pnl_delta)
    col4.metric("Total ROI", f"{portfolio_roi:.2f}%", delta=f"{portfolio_roi:.2f}%")

    if view_mode == "👀 Market Watchlist" and total_invested == 0:
        col5.metric("1D % Change (Avg)", f"{day_change_pct:+.2f}%", delta=f"{day_change_pct:+.2f}%")
    else:
        day_delta_str = f"-₹{abs(day_pnl):,.2f}" if day_pnl < 0 else f"+₹{abs(day_pnl):,.2f}"
        col5.metric("1D % Change", f"{day_change_pct:+.2f}%", delta=day_delta_str)

    st.divider()

    visible_cols_set = set(load_column_prefs())

    if view_mode == "💼 Demat Holdings":
        if not holdings_df.empty:
            disp = build_demat_display_frame(holdings_df)
            active_cols = [c for c in ORDERED_COLUMNS if c in visible_cols_set and c in disp.columns]
            st.dataframe(style_demat_table(disp[active_cols]), column_order=active_cols, width="stretch", hide_index=True)
        else:
            st.info("No delivery holdings currently in your Angel One account.")
    else:
        if not watchlist_df.empty:
            disp = build_watchlist_display_frame(watchlist_df)
            active_cols = [c for c in ORDERED_COLUMNS if c in visible_cols_set and c in disp.columns]
            st.dataframe(style_watchlist_table(disp[active_cols]), column_order=active_cols, width="stretch", hide_index=True, height=500)
        else:
            st.info("Watchlist is empty. Use the sidebar on the left to add tickers.")

live_dashboard()