import pandas as pd
from demat_display import ORDERED_COLUMNS, _safe_numeric

def build_watchlist_display_frame(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=ORDERED_COLUMNS)

    rows = df.copy().reset_index(drop=True)

    exchange = rows.get("Exchange", pd.Series(["NSE"] * len(rows), index=rows.index)).fillna("NSE").astype(str)
    cmp = _safe_numeric(rows.get("CMP", pd.NA), index=rows.index)
    pc = _safe_numeric(rows.get("PC", pd.NA), index=rows.index)
    day_high = _safe_numeric(rows.get("Day High", pd.NA), index=rows.index)
    volume = _safe_numeric(rows.get("Volume", pd.NA), index=rows.index)
    
    quantity = _safe_numeric(rows.get("Quantity", 0), index=rows.index)
    buy_price = _safe_numeric(rows.get("Buy Price", 0), index=rows.index)
    buy_date = rows.get("Buy Date", pd.Series([pd.NA] * len(rows), index=rows.index))
    
    sell_price = _safe_numeric(rows.get("Sell Price", pd.NA), index=rows.index)
    side = rows.get("Side", pd.Series(["LONG"] * len(rows), index=rows.index)).astype(str).str.upper()
    status = rows.get("Status", pd.Series(["OPEN"] * len(rows), index=rows.index)).astype(str).str.upper()

    d_pct = _safe_numeric(rows.get("D%", pd.NA), index=rows.index)
    dh_pct = _safe_numeric(rows.get("DH%", pd.NA), index=rows.index)
    s_alert = _safe_numeric(rows.get("SAlert", pd.NA), index=rows.index)

    t_buy_price = quantity * buy_price
    t_cmp_v = pd.Series(0.0, index=rows.index)
    profit = pd.Series(0.0, index=rows.index)

    for idx in rows.index:
        q = quantity.loc[idx]
        bp = buy_price.loc[idx]
        sp = sell_price.loc[idx]
        c = cmp.loc[idx]
        s = side.loc[idx]
        st_val = status.loc[idx]

        if pd.isna(q) or q == 0:
            continue

        if st_val == "CLOSED" and not pd.isna(sp):
            t_cmp_v.loc[idx] = sp * q
            if s == "SHORT":
                profit.loc[idx] = (bp - sp) * q
            else:
                profit.loc[idx] = (sp - bp) * q
        else:
            t_cmp_v.loc[idx] = c * q
            if s == "SHORT":
                profit.loc[idx] = (bp - c) * q
            else:
                profit.loc[idx] = (c - bp) * q

    pct_profit = pd.Series([pd.NA] * len(rows), index=rows.index)
    valid_profit = t_buy_price.notna() & (t_buy_price != 0)
    pct_profit.loc[valid_profit] = (profit.loc[valid_profit] / t_buy_price.loc[valid_profit]) * 100

    output = pd.DataFrame({
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
        "T. Buy Price": t_buy_price,
        "T. Sell Price": pd.Series([pd.NA] * len(rows), index=rows.index),
        "T-CMP-V": t_cmp_v,
        "Profit": profit,
        "% Profit": pct_profit,
        "DH%": dh_pct,
        "D%": d_pct,
        "SAlert": s_alert,
        "placeholder": pd.Series([pd.NA] * len(rows), index=rows.index),
        "PMC": pd.Series([pd.NA] * len(rows), index=rows.index),
        "M %": pd.Series([pd.NA] * len(rows), index=rows.index),
        "Vol%": pd.Series([pd.NA] * len(rows), index=rows.index),
        "Volume": volume,
        "PD Volume": pd.Series([pd.NA] * len(rows), index=rows.index),
    }, columns=ORDERED_COLUMNS)

    return output.fillna("NA")

def style_watchlist_table(df):
    if df is None or df.empty: return df

    def format_2_decimals(val):
        if isinstance(val, float) and not pd.isna(val): return f"{val:.2f}"
        return val

    styler = df.style.format(format_2_decimals)

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
                        styles[idx] = "background-color: #ca8a04; color: white; font-weight: bold;"
                except (ValueError, TypeError): pass
                
        return styles

    return styler.apply(highlight_cells, axis=1)