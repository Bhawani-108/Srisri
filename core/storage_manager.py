import os
import pandas as pd
from core.formula_engine import get_visible_columns, set_column_visibility

CSV_FILE = "portfolio.csv"
WATCHLIST_FILE = "watchlist.txt"
MOCK_PORTFOLIO_FILE = "mock_portfolio.csv"

_CACHED_MOCK_PORTFOLIO = None
_LAST_MOCK_MTIME = 0

def load_column_prefs():
    """
    Loads active visible columns directly from column_config.json via formula_engine.
    """
    return get_visible_columns()

def save_column_prefs(visible_cols_list):
    """
    Persists column visibility flags directly into column_config.json via formula_engine.
    """
    set_column_visibility(visible_cols_list)

def ensure_mock_portfolio_file():
    """
    Ensures mock_portfolio.csv exists with the standard schema.
    """
    if not os.path.exists(MOCK_PORTFOLIO_FILE):
        pd.DataFrame(
            columns=["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status", "PD Volume"]
        ).to_csv(MOCK_PORTFOLIO_FILE, index=False)

def load_mock_portfolio():
    """
    Reads mock portfolio overrides with file mtime caching to minimize disk I/O.
    """
    global _CACHED_MOCK_PORTFOLIO, _LAST_MOCK_MTIME
    ensure_mock_portfolio_file()
    try:
        current_mtime = os.stat(MOCK_PORTFOLIO_FILE).st_mtime_ns
    except OSError:
        current_mtime = -1

    if _CACHED_MOCK_PORTFOLIO is not None and current_mtime == _LAST_MOCK_MTIME:
        return _CACHED_MOCK_PORTFOLIO

    schema = ["Stock Name", "Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status"]
    try:
        df = pd.read_csv(MOCK_PORTFOLIO_FILE)
        if df.empty or "Stock Name" not in df.columns:
            _CACHED_MOCK_PORTFOLIO = pd.DataFrame(columns=schema)
        else:
            df["Stock Name"] = df["Stock Name"].astype(str).str.upper()
            _CACHED_MOCK_PORTFOLIO = df.reset_index(drop=True)
    except Exception:
        _CACHED_MOCK_PORTFOLIO = pd.DataFrame(columns=schema)
    
    _LAST_MOCK_MTIME = current_mtime
    return _CACHED_MOCK_PORTFOLIO

def save_mock_portfolio(df):
    """
    Persists updated mock trade positions to disk.
    """
    df.to_csv(MOCK_PORTFOLIO_FILE, index=False)

def load_watchlist_tickers():
    """
    Reads watchlist tickers from watchlist.txt.
    """
    if not os.path.exists(WATCHLIST_FILE):
        return []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return [line.strip().upper() for line in f if line.strip()]

def save_watchlist_tickers(tickers):
    """
    Overwrites watchlist.txt with the provided list of tickers.
    """
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(tickers) + ("\n" if tickers else ""))

def append_watchlist_tickers(tickers):
    """
    Appends new tickers to watchlist.txt.
    """
    with open(WATCHLIST_FILE, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(tickers) + "\n")