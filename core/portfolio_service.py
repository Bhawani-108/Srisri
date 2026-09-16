import os
import pandas as pd
import backend_updater
from core.storage_manager import CSV_FILE, load_mock_portfolio

def apply_mock_portfolio(df):
    if df is None or df.empty: return df
    mock_df = load_mock_portfolio()
    if mock_df.empty or "Stock Name" not in df.columns: return df

    df = df.copy()
    mock_mapping = mock_df.set_index("Stock Name")

    # Unified override: applies across both Watchlist and Holdings if specified
    for col in ["Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status", "PD Volume"]:
        if col in mock_mapping.columns:
            mapped = df["Stock Name"].map(mock_mapping[col])
            if col not in df.columns:
                df[col] = mapped
            else:
                df[col] = mapped.where(mapped.notna(), df[col])
            
    return df

def ensure_backend_data_loaded():
    try:
        if not os.path.exists(CSV_FILE):
            with open(CSV_FILE, "w", encoding="utf-8") as f: f.write("")
        if os.path.getsize(CSV_FILE) == 0:
            current_portfolio = backend_updater.sync_portfolio_registry(None)
            backend_updater.stream_tick_cycle(current_portfolio)
    except Exception as e:
        print(f"Preflight sync warning: {e}")

def get_clean_data():
    if not os.path.exists(CSV_FILE): return pd.DataFrame()
    try:
        df = pd.read_csv(CSV_FILE)
    except Exception:
        return pd.DataFrame()

    if df.empty: return pd.DataFrame()

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
        if 'PD Volume' not in df.columns: df['PD Volume'] = pd.NA
        
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
                df.loc[idx, 'Net P&L'] = (bp - sp) * q if s == "SHORT" else (sp - bp) * q
            else:
                df.loc[idx, 'Current Value'] = c * q
                df.loc[idx, 'Net P&L'] = (bp - c) * q if s == "SHORT" else (c - bp) * q
                    
        is_hold = df['Type'] == 'Holding'
        df.loc[is_hold, 'Current Value'] = df.loc[is_hold, 'Quantity'] * df.loc[is_hold, 'CMP']
        df.loc[is_hold, 'Net P&L'] = df.loc[is_hold, 'Current Value'] - df.loc[is_hold, 'Total Invested']
        
    return df