import time
import streamlit as st
import backend_updater
from core.symbol_catalog import get_all_indexed_symbols
from core.storage_manager import load_watchlist_tickers, save_watchlist_tickers

def render_sidebar():
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
                if selected_item is None:
                    st.warning("Please choose a valid symbol.")
                else:
                    new_ticker = str(selected_item["unique_key"]).strip().upper()
                    existing = load_watchlist_tickers()

                    if new_ticker in existing:
                        st.warning(f"'{new_ticker}' is already on the watchlist.")
                    else:
                        existing.append(new_ticker)
                        save_watchlist_tickers(existing)
                        backend_updater.LAST_WATCHLIST_MTIME = 0
                        st.success(f"Added '{new_ticker}'. Syncing live feed...")
                        time.sleep(0.5)
                        st.rerun()

        st.divider()

        active_watchlist = load_watchlist_tickers()
        if active_watchlist:
            st.subheader("Remove Ticker")
            stock_to_remove = st.selectbox("Select ticker to remove", active_watchlist)
            if st.button("🗑️ Delete from Watchlist", width="stretch"):
                updated_list = [s for s in active_watchlist if s != stock_to_remove]
                save_watchlist_tickers(updated_list)
                backend_updater.LAST_WATCHLIST_MTIME = 0
                st.success(f"Removed '{stock_to_remove}'")
                time.sleep(0.5)
                st.rerun()