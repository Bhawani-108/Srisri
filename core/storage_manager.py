import os
import json
import pandas as pd
from views.demat_display import ORDERED_COLUMNS

CSV_FILE = "portfolio.csv"
WATCHLIST_FILE = "watchlist.txt"
MOCK_PORTFOLIO_FILE = "mock_portfolio.csv"
COL_PREFS_FILE = "column_prefs.json"

_CACHED_MOCK_PORTFOLIO = None
_LAST_MOCK_MTIME = 0

def load_column_prefs():
    if os.path.exists(COL_PREFS_FILE):
        try:
            with open(COL_PREFS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if "visible_columns" in data and isinstance(data["visible_columns"], list):
                        return data["visible_columns"]
                    elif "demat" in data and isinstance(data["demat"], list):
                        return data["demat"]
                elif isinstance(data, list):
                    return data
        except Exception:
            pass
    return list(ORDERED_COLUMNS)

def save_column_prefs(visible_cols_list):
    try:
        with open(COL_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump({"visible_columns": visible_cols_list}, f, indent=4)
    except Exception:
        pass

def ensure_mock_portfolio_file():
    if not os.path.exists(MOCK_PORTFOLIO_FILE):
        pd.DataFrame(columns=["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status"]).to_csv(MOCK_PORTFOLIO_FILE, index=False)

def load_mock_portfolio():
    global _CACHED_MOCK_PORTFOLIO, _LAST_MOCK_MTIME
    ensure_mock_portfolio_file()
    try: current_mtime = os.stat(MOCK_PORTFOLIO_FILE).st_mtime_ns
    except OSError: current_mtime = -1

    if _CACHED_MOCK_PORTFOLIO is not None and current_mtime == _LAST_MOCK_MTIME:
        return _CACHED_MOCK_PORTFOLIO

    try:
        df = pd.read_csv(MOCK_PORTFOLIO_FILE)
        if df.empty or "Stock Name" not in df.columns:
            _CACHED_MOCK_PORTFOLIO = pd.DataFrame(columns=["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status"])
        else:
            df["Stock Name"] = df["Stock Name"].astype(str).str.upper()
            _CACHED_MOCK_PORTFOLIO = df.reset_index(drop=True)
    except Exception:
        _CACHED_MOCK_PORTFOLIO = pd.DataFrame(columns=["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status"])
    
    _LAST_MOCK_MTIME = current_mtime
    return _CACHED_MOCK_PORTFOLIO

def save_mock_portfolio(df):
    df.to_csv(MOCK_PORTFOLIO_FILE, index=False)

def load_watchlist_tickers():
    if not os.path.exists(WATCHLIST_FILE):
        return []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return [line.strip().upper() for line in f if line.strip()]

def save_watchlist_tickers(tickers):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(tickers) + ("\n" if tickers else ""))

def append_watchlist_tickers(tickers):
    with open(WATCHLIST_FILE, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(tickers) + "\n")