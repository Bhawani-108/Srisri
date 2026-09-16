import time
import pandas as pd
import streamlit as st
import backend_updater
from core.storage_manager import load_mock_portfolio, save_mock_portfolio, load_watchlist_tickers, save_watchlist_tickers, append_watchlist_tickers
from core.portfolio_service import get_clean_data

def render_mock_portfolio_editor():
    with st.expander("📝 Edit Mock Portfolio (Paper Trading)", expanded=False):
        st.subheader("📥 Import CSV")
        uploaded_mock_file = st.file_uploader(
            "Upload CSV to add or replace mock positions and watchlist",
            type=["csv"],
            key="mock_portfolio_csv_uploader"
        )

        if uploaded_mock_file is not None:
            col_append, col_replace = st.columns(2)
            with col_append:
                do_append = st.button("➕ Append Stocks", width="stretch")
            with col_replace:
                do_replace = st.button("🚀 Replace Entire Watchlist", width="stretch")

            if do_append or do_replace:
                try:
                    import_df = pd.read_csv(uploaded_mock_file)
                    import_df.columns = [str(c).strip() for c in import_df.columns]

                    stock_col = next((c for c in ["Stock Name", "Symbol", "Ticker"] if c in import_df.columns), None)
                    if not stock_col:
                        stock_col = next((c for c in import_df.columns if "STOCK" in c.upper() or "NAME" in c.upper()), import_df.columns[0])

                    import_df["Stock Name"] = import_df[stock_col].astype(str).str.strip().str.upper()
                    import_df = import_df[import_df["Stock Name"].str.len() > 0]
                    import_df = import_df[~import_df["Stock Name"].isin(["NAN", "NONE", "NULL", "", "NA"])]
                    import_df = import_df.dropna(subset=["Stock Name"]).drop_duplicates(subset=["Stock Name"], keep="last")

                    if import_df.empty:
                        st.error("No valid stock entries found in the uploaded CSV.")
                    else:
                        exch_col = next((c for c in ["NSE/BSE etc", "Exchange", "Exch"] if c in import_df.columns), None)
                        import_df["Exchange"] = import_df[exch_col].astype(str).str.strip().str.upper().replace({'NAN': 'NSE', 'NONE': 'NSE', '': 'NSE'}) if exch_col else "NSE"

                        def safe_parse_side(val):
                            if pd.isna(val): return None
                            s = str(val).strip().upper()
                            if s in ["", "NAN", "NONE", "NULL", "", "NA"]: return None
                            return "SHORT" if "SHORT" in s else "LONG"

                        def safe_parse_status(val):
                            if pd.isna(val): return None
                            s = str(val).strip().upper()
                            if s in ["", "NAN", "NONE", "NULL", "", "NA"]: return None
                            return "CLOSED" if "CLOSE" in s else "OPEN"

                        import_df["Side"] = import_df["Side"].apply(safe_parse_side) if "Side" in import_df.columns else None
                        import_df["Status"] = import_df["Status"].apply(safe_parse_status) if "Status" in import_df.columns else None
                        import_df["Quantity"] = pd.to_numeric(import_df.get("Quantity", pd.NA), errors="coerce")
                        import_df["Buy Price"] = pd.to_numeric(import_df.get("Buy Price", pd.NA), errors="coerce")
                        import_df["Sell Price"] = pd.to_numeric(import_df.get("Sell Price", pd.NA), errors="coerce")
                        import_df["PD Volume"] = pd.to_numeric(import_df.get("PD Volume", pd.NA), errors="coerce")

                        if "Buy Date" in import_df.columns:
                            parsed_date = pd.to_datetime(import_df["Buy Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                            import_df["Buy Date"] = parsed_date.where(parsed_date.notna(), None)
                        else:
                            import_df["Buy Date"] = None

                        cols_to_keep = ["Stock Name", "Side", "Status", "Quantity", "Buy Price", "Sell Price", "Buy Date", "PD Volume"]
                        prepared_import = import_df[cols_to_keep].copy()
                        new_watchlist_entries = [f"{str(r['Stock Name']).strip()}:{str(r.get('Exchange', 'NSE')).strip()}" for _, r in import_df.iterrows()]

                        if do_replace:
                            save_mock_portfolio(prepared_import)
                            save_watchlist_tickers(new_watchlist_entries)
                            msg = f"Successfully replaced watchlist with {len(prepared_import)} stocks!"
                        else:
                            existing_mock = load_mock_portfolio()
                            merged_mock = existing_mock[~existing_mock["Stock Name"].isin(prepared_import["Stock Name"])].copy()
                            merged_mock = pd.concat([merged_mock, prepared_import], ignore_index=True)
                            save_mock_portfolio(merged_mock)

                            existing_wl = load_watchlist_tickers()
                            existing_set = set(existing_wl)
                            added_tickers = []
                            for entry in new_watchlist_entries:
                                sym = entry.split(":")[0]
                                if entry not in existing_set and sym not in existing_set:
                                    added_tickers.append(entry)
                                    existing_set.add(entry)

                            if added_tickers:
                                append_watchlist_tickers(added_tickers)

                            msg = f"Appended/updated {len(prepared_import)} stocks in mock portfolio!"

                        backend_updater.LAST_WATCHLIST_MTIME = 0
                        st.success(f"{msg} Syncing live feed...")
                        time.sleep(1.0)
                        st.rerun()

                except Exception as ex:
                    st.error(f"Failed to process CSV file: {ex}")

        st.divider()

        # MANUAL DATA EDITOR
        init_df = get_clean_data()
        init_watchlist = init_df[init_df['Type'] == 'Watchlist'].copy() if not init_df.empty and 'Type' in init_df.columns else pd.DataFrame()
        
        if not init_watchlist.empty:
            for col in ["Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status", "PD Volume"]:
                if col not in init_watchlist.columns: 
                    init_watchlist[col] = None
                    
            mock_editor_df = init_watchlist[["Stock Name", "Side", "Status", "Quantity", "Buy Price", "Sell Price", "Buy Date", "PD Volume"]].copy()
            
            def clean_editor_col(val, pos_val, neg_val):
                if pd.isna(val) or str(val).strip().upper() in ["", "NAN", "NONE", "NULL", "", "NA"]:
                    return None
                s = str(val).strip().upper()
                return pos_val if pos_val in s else neg_val

            mock_editor_df["Side"] = mock_editor_df["Side"].apply(lambda x: clean_editor_col(x, "SHORT", "LONG"))
            mock_editor_df["Status"] = mock_editor_df["Status"].apply(lambda x: clean_editor_col(x, "CLOSED", "OPEN"))
            mock_editor_df["Quantity"] = pd.to_numeric(mock_editor_df["Quantity"], errors="coerce")
            mock_editor_df["Buy Price"] = pd.to_numeric(mock_editor_df["Buy Price"], errors="coerce")
            mock_editor_df["Sell Price"] = pd.to_numeric(mock_editor_df["Sell Price"], errors="coerce")
            mock_editor_df["PD Volume"] = pd.to_numeric(mock_editor_df["PD Volume"], errors="coerce")
            
            if "Buy Date" in mock_editor_df.columns:
                mock_editor_df["Buy Date"] = mock_editor_df["Buy Date"].astype("string")

            edited_mock = st.data_editor(
                mock_editor_df, 
                width="stretch", 
                hide_index=True, 
                disabled=["Stock Name"], 
                column_config={
                    "Side": st.column_config.SelectboxColumn("Side", options=["LONG", "SHORT"], required=False),
                    "Status": st.column_config.SelectboxColumn("Status", options=["OPEN", "CLOSED"], required=False),
                    "Quantity": st.column_config.NumberColumn("Quantity"),
                    "Buy Price": st.column_config.NumberColumn("Buy Price"),
                    "Sell Price": st.column_config.NumberColumn("Sell Price"),
                    "Buy Date": st.column_config.TextColumn("Buy Date"),
                    "PD Volume": st.column_config.NumberColumn("PD Volume"),
                },
                key="mock_editor"
            )
            
            if not edited_mock.equals(mock_editor_df):
                manual_rows = edited_mock.copy()
                manual_rows["Stock Name"] = manual_rows["Stock Name"].astype(str).str.upper()
                if "Buy Date" in manual_rows.columns: 
                    manual_rows["Buy Date"] = pd.to_datetime(manual_rows["Buy Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                
                save_mock_portfolio(manual_rows)
                st.rerun()
        else:
            st.info("Watchlist is empty. Upload a CSV above or use the sidebar to add tickers.")