import os
import pandas as pd
from core.formula_engine import get_visible_columns, set_column_visibility

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_FILE = os.path.join(APP_DIR, "portfolio.csv")
WATCHLIST_FILE = os.path.join(APP_DIR, "watchlist.txt")
MOCK_PORTFOLIO_FILE = os.path.join(APP_DIR, "mock_portfolio.csv")

_CACHED_MOCK_PORTFOLIO = None
_LAST_MOCK_MTIME = 0
MOCK_SCHEMA = ["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status", "PD Volume"]

def load_column_prefs():
    return get_visible_columns()

def save_column_prefs(visible_cols_list):
    set_column_visibility(visible_cols_list)

def ensure_mock_portfolio_file():
    if not os.path.exists(MOCK_PORTFOLIO_FILE):
        pd.DataFrame(columns=MOCK_SCHEMA).to_csv(MOCK_PORTFOLIO_FILE, index=False)

def load_mock_portfolio():
    global _CACHED_MOCK_PORTFOLIO, _LAST_MOCK_MTIME
    ensure_mock_portfolio_file()
    try:
        current_mtime = os.stat(MOCK_PORTFOLIO_FILE).st_mtime_ns
    except OSError:
        current_mtime = -1

    if _CACHED_MOCK_PORTFOLIO is not None and current_mtime == _LAST_MOCK_MTIME:
        return _CACHED_MOCK_PORTFOLIO

    try:
        df = pd.read_csv(MOCK_PORTFOLIO_FILE)
        if df.empty or "Stock Name" not in df.columns:
            _CACHED_MOCK_PORTFOLIO = pd.DataFrame(columns=MOCK_SCHEMA)
        else:
            df["Stock Name"] = df["Stock Name"].astype(str).str.upper()
            for col in MOCK_SCHEMA:
                if col not in df.columns:
                    df[col] = 0 if col == "PD Volume" else pd.NA
            _CACHED_MOCK_PORTFOLIO = df.reset_index(drop=True)
    except Exception:
        _CACHED_MOCK_PORTFOLIO = pd.DataFrame(columns=MOCK_SCHEMA)
    
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