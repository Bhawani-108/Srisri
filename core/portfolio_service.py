import os
import numpy as np
import pandas as pd
from brokers.config import get_active_broker_name
from core.storage_manager import load_mock_portfolio, APP_DIR


def get_broker_csv(broker_name: str | None = None) -> str:
    name = (broker_name or get_active_broker_name()).lower().strip()
    return os.path.join(APP_DIR, f"portfolio_{name}.csv")


def clean_token(val) -> str:
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return "" if s.lower() in ("nan", "none", "") else s


def apply_mock_portfolio(df):
    if df is None or df.empty:
        return df
    mock_df = load_mock_portfolio()
    if mock_df.empty or "Stock Name" not in df.columns:
        return df

    df = df.copy()
    mock_mapping = mock_df.set_index("Stock Name")

    override_cols = ["Buy Date", "Quantity", "Buy Price", "Sell Price", "Side", "Status", "PD Volume"]
    for col in override_cols:
        if col in mock_mapping.columns:
            mapped = df["Stock Name"].map(mock_mapping[col])
            if col not in df.columns:
                df[col] = mapped
            else:
                df[col] = mapped.where(mapped.notna(), df[col])

    return df


def ensure_backend_data_loaded():
    target_csv = get_broker_csv()
    try:
        if not os.path.exists(target_csv):
            with open(target_csv, "w", encoding="utf-8") as f:
                f.write("")
    except Exception as e:
        print(f"Preflight sync warning: {e}")


def get_clean_data():
    target_csv = get_broker_csv()
    if not os.path.exists(target_csv):
        return pd.DataFrame()
    try:
        if os.path.getsize(target_csv) == 0:
            return pd.DataFrame()
        # Force Token column to be parsed as string to prevent float conversions (e.g., 3045 -> 3045.0)
        df = pd.read_csv(target_csv, dtype={"Token": str})
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    if "Token" in df.columns:
        df["Token"] = df["Token"].apply(clean_token)

    df = apply_mock_portfolio(df)

    if not df.empty and "CMP" in df.columns:
        df["Quantity"] = pd.to_numeric(df.get("Quantity", 0), errors="coerce").fillna(0.0)
        buy_price_series = pd.to_numeric(df.get("Buy Price", pd.NA), errors="coerce")
        avg_price_series = pd.to_numeric(df.get("Average Price", 0), errors="coerce").fillna(0.0)
        df["Effective_Buy_Price"] = buy_price_series.combine_first(avg_price_series).fillna(0.0)
        df["CMP"] = pd.to_numeric(df["CMP"], errors="coerce").fillna(0.0)
        df["PC"] = pd.to_numeric(df.get("PC", 0), errors="coerce").fillna(0.0)

        if "Sell Price" not in df.columns:
            df["Sell Price"] = pd.NA
        if "Side" not in df.columns:
            df["Side"] = "LONG"
        if "Status" not in df.columns:
            df["Status"] = "OPEN"
        if "PD Volume" not in df.columns:
            df["PD Volume"] = pd.NA

        df["Total Invested"] = df["Quantity"] * df["Effective_Buy_Price"]

        # Vectorized Net P&L and Current Value calculation for Watchlist vs Holdings
        is_wl = df["Type"] == "Watchlist"
        df["Current Value"] = df["Total Invested"]
        df["Net P&L"] = 0.0

        for idx in df[is_wl].index:
            q = df.loc[idx, "Quantity"]
            bp = df.loc[idx, "Effective_Buy_Price"]
            sp = pd.to_numeric(df.loc[idx, "Sell Price"], errors="coerce")
            c = df.loc[idx, "CMP"]
            s = str(df.loc[idx, "Side"]).upper()
            st_val = str(df.loc[idx, "Status"]).upper()

            if st_val == "CLOSED" and not pd.isna(sp):
                df.loc[idx, "Current Value"] = sp * q
                df.loc[idx, "Net P&L"] = (bp - sp) * q if s == "SHORT" else (sp - bp) * q
            else:
                df.loc[idx, "Current Value"] = c * q
                df.loc[idx, "Net P&L"] = (bp - c) * q if s == "SHORT" else (c - bp) * q

        is_hold = df["Type"] == "Holding"
        df.loc[is_hold, "Current Value"] = df.loc[is_hold, "Quantity"] * df.loc[is_hold, "CMP"]
        df.loc[is_hold, "Net P&L"] = df.loc[is_hold, "Current Value"] - df.loc[is_hold, "Total Invested"]

        # Recalculate 1D % using clean Previous Close
        pc_series = df["PC"]
        cmp_series = df["CMP"]
        df["D%"] = np.where(pc_series > 0, ((cmp_series - pc_series) / pc_series) * 100, 0.0)

    return df