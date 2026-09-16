import pandas as pd

ORDERED_COLUMNS = [
    "Index", "Stock Name", "NSE/BSE etc", "EQ etc", "CMP", "PC", "Quantity",
    "Buy Date", "Buy Price", "Sell Date", "Sell Price", "T. Buy Price", "T. Sell Price",
    "T-CMP-V", "Profit", "% Profit", "DH%", "D%", "SAlert", "placeholder",
    "PMC", "M %", "Vol%", "Volume", "PD Volume"
]

def _safe_numeric(value, index=None):
    result = pd.to_numeric(value, errors="coerce")
    if isinstance(result, pd.Series): return result
    if index is None: return pd.Series([result])
    return pd.Series([result] * len(index), index=index)

def build_demat_display_frame(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=ORDERED_COLUMNS)

    rows = df.copy().reset_index(drop=True)

    exchange = rows.get("Exchange", pd.Series(["NSE"] * len(rows), index=rows.index)).fillna("NSE").astype(str)
    cmp = _safe_numeric(rows.get("CMP", pd.NA), index=rows.index)
    pc = _safe_numeric(rows.get("PC", pd.NA), index=rows.index)
    day_high = _safe_numeric(rows.get("Day High", pd.NA), index=rows.index)
    volume = _safe_numeric(rows.get("Volume", pd.NA), index=rows.index)
    
    quantity = _safe_numeric(rows.get("Quantity", 0), index=rows.index)
    avg_price = _safe_numeric(rows.get("Average Price", 0), index=rows.index)
    buy_price_override = _safe_numeric(rows.get("Buy Price", pd.NA), index=rows.index)
    
    buy_price = buy_price_override.combine_first(avg_price)
    buy_price = pd.to_numeric(buy_price, errors="coerce")
    
    buy_date = rows.get("Buy Date", pd.Series([pd.NA] * len(rows), index=rows.index))
    
    d_pct = _safe_numeric(rows.get("D%", pd.NA), index=rows.index)
    dh_pct = _safe_numeric(rows.get("DH%", pd.NA), index=rows.index)
    s_alert = _safe_numeric(rows.get("SAlert", pd.NA), index=rows.index)

    t_buy_price = quantity * buy_price
    t_cmp_v = cmp * quantity
    profit = t_cmp_v - t_buy_price

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
        "Sell Price": pd.Series([pd.NA] * len(rows), index=rows.index),
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

    # First fill NaNs with NA for string columns, but KEEP numeric columns strictly numeric
    output = output.fillna("NA")
    
    # Force numeric columns back to float/int so PyArrow never chokes on object/string types
    numeric_cols = ["CMP", "PC", "Quantity", "Buy Price", "Sell Price", "T. Buy Price", "T. Sell Price", "T-CMP-V", "Profit", "% Profit", "DH%", "D%", "SAlert", "Volume"]
    for col in numeric_cols:
        if col in output.columns:
            output[col] = pd.to_numeric(output[col].replace("NA", pd.NA), errors="coerce")

    return output

def style_demat_table(df):
    if df is None or df.empty: return df

    def format_2_decimals(val):
        if isinstance(val, float) and not pd.isna(val): return f"{val:.2f}"
        return val

    styler = df.style.format(format_2_decimals, na_rep="NA")

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