import numpy as np
import pandas as pd
from core.formula_engine import evaluate_formulas, get_configured_column_order

ORDERED_COLUMNS = get_configured_column_order()

def _safe_numeric(value, index=None):
    result = pd.to_numeric(value, errors="coerce")
    if isinstance(result, pd.Series): return result
    if index is None: return pd.Series([result])
    return pd.Series([result] * len(index), index=index)

def build_watchlist_display_frame(df):
    current_order = get_configured_column_order()
    if df is None or df.empty:
        return pd.DataFrame(columns=current_order)

    rows = df.copy().reset_index(drop=True)

    exchange = rows.get("Exchange", pd.Series(["NSE"] * len(rows), index=rows.index)).fillna("NSE").astype(str)
    cmp = _safe_numeric(rows.get("CMP", pd.NA), index=rows.index)
    pc = _safe_numeric(rows.get("PC", pd.NA), index=rows.index)
    day_high = _safe_numeric(rows.get("Day High", pd.NA), index=rows.index)
    volume = _safe_numeric(rows.get("Volume", pd.NA), index=rows.index)
    
    quantity = _safe_numeric(rows.get("Quantity", 0), index=rows.index)
    buy_price = _safe_numeric(rows.get("Buy Price", pd.NA), index=rows.index)
    buy_date = rows.get("Buy Date", pd.Series([pd.NA] * len(rows), index=rows.index))
    
    sell_price = _safe_numeric(rows.get("Sell Price", pd.NA), index=rows.index)
    side = rows.get("Side", pd.Series(["LONG"] * len(rows), index=rows.index)).astype(str).str.upper()
    status = rows.get("Status", pd.Series(["OPEN"] * len(rows), index=rows.index)).astype(str).str.upper()

    d_pct = _safe_numeric(rows.get("D%", pd.NA), index=rows.index)
    dh_pct = _safe_numeric(rows.get("DH%", pd.NA), index=rows.index)
    period_pcts = {
        name: _safe_numeric(rows.get(name, pd.NA), index=rows.index)
        for name in ("1D%", "2D%", "3D%", "4D%", "1W%", "1M%")
    }
    s_alert = _safe_numeric(rows.get("SAlert", pd.NA), index=rows.index)
    pd_vol = _safe_numeric(rows.get("PD Volume", 0), index=rows.index).fillna(0)

    # Use incoming broker column or default to Angel One
    broker_series = rows.get("Broker", pd.Series(["Angel One"] * len(rows), index=rows.index)).fillna("Angel One")

    base_frame = pd.DataFrame({
        "Index": range(1, len(rows) + 1),
        "Stock Name": rows.get("Stock Name", ""),
        "NSE/BSE etc": exchange,
        "EQ etc": "EQ",
        "CMP": cmp,
        "PC": pc,
        "Quantity": quantity,
        "Buy Date": buy_date,
        "Buy Price": buy_price,
        "Sell Date": pd.Series([pd.NA] * len(rows), index=rows.index),
        "Sell Price": sell_price,
        "DH%": dh_pct,
        "D%": d_pct,
        **period_pcts,
        "SAlert": s_alert,
        "placeholder": pd.Series([pd.NA] * len(rows), index=rows.index),
        "PMC": pd.Series([pd.NA] * len(rows), index=rows.index),
        "M %": pd.Series([pd.NA] * len(rows), index=rows.index),
        "Volume": volume,
        "PD Volume": pd_vol,
        "Broker": broker_series,
    })

    # Override CMP with Sell Price for closed mock positions
    for idx in rows.index:
        q = quantity.loc[idx]
        sp = sell_price.loc[idx]
        st_val = status.loc[idx]
        if pd.notna(q) and q > 0 and st_val == "CLOSED" and pd.notna(sp):
            base_frame.loc[idx, "CMP"] = sp

    output = evaluate_formulas(base_frame)

    if "Profit" in output.columns:
        for idx in rows.index:
            if side.loc[idx] == "SHORT" and pd.notna(output.loc[idx, "Profit"]):
                output.loc[idx, "Profit"] = -output.loc[idx, "Profit"]
                if "% Profit" in output.columns and pd.notna(output.loc[idx, "% Profit"]):
                    output.loc[idx, "% Profit"] = -output.loc[idx, "% Profit"]

    non_numeric = {"Index", "Stock Name", "NSE/BSE etc", "EQ etc", "Buy Date", "Sell Date", "placeholder", "PMC", "M %", "Vol%", "PD Volume", "Broker"}
    for col in output.columns:
        if col not in non_numeric:
            output[col] = pd.to_numeric(output[col], errors="coerce")

    if "Index" in output.columns:
        output["Index"] = pd.to_numeric(output["Index"], errors="coerce").fillna(0).astype(int)

    return output

def style_watchlist_table(df):
    if df is None or df.empty: return df

    def format_cells(val):
        if isinstance(val, (int, np.integer)):
            return f"{val}"
        if isinstance(val, (float, np.floating)) and not pd.isna(val):
            return f"{val:.2f}"
        return val

    styler = df.style.format(format_cells, na_rep="NA")

    def highlight_cells(row):
        styles = ['' for _ in row]
        for idx, col_name in enumerate(row.index):
            val = row[col_name]
            
            if col_name in ["D%", "% Profit"]:
                try:
                    num = float(val)
                    if num > 0: styles[idx] = "background-color: #1f9d55; color: white;"
                    elif num < 0: styles[idx] = "background-color: #d64545; color: white;"
                    else: styles[idx] = "background-color: #e5e7eb; color: #111827;"
                except (ValueError, TypeError): pass
            
            elif col_name == "SAlert":
                try:
                    num = float(val)
                    if num < -5:
                        styles[idx] = "background-color: #ca8a04; color: white;"
                except (ValueError, TypeError): pass
                
        return styles

    return styler.apply(highlight_cells, axis=1)