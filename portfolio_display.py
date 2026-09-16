import pandas as pd

ORDERED_COLUMNS = [
    "Index",
    "Stock Name",
    "NSE/BSE etc",
    "EQ etc",
    "CMP",
    "PC",
    "Quantity",
    "Buy Date",
    "Buy Price",
    "Sell Date",
    "Sell Price",
    "T. Buy Price",
    "T. Sell Price",
    "T-CMP-V",
    "Profit",
    "% Profit",
    "DH%",
    "D%",
    "SAlert",
    "placeholder",
    "PMC",
    "M %",
    "Vol%",
    "Volume",
    "PD Volume",
]


def _safe_numeric(value, index=None):
    result = pd.to_numeric(value, errors="coerce")
    if isinstance(result, pd.Series):
        return result
    if index is None:
        return pd.Series([result])
    return pd.Series([result] * len(index), index=index)


def build_ordered_display_frame(df, row_type="Holding"):
    if df is None or df.empty:
        return pd.DataFrame(columns=ORDERED_COLUMNS)

    rows = df.copy()
    rows = rows.reset_index(drop=True)

    exchange = rows.get("Exchange", pd.Series(["NSE"] * len(rows), index=rows.index))
    exchange = exchange.fillna("NSE").astype(str)

    cmp = _safe_numeric(rows.get("CMP", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    pc = _safe_numeric(rows.get("PC", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    day_high = _safe_numeric(rows.get("Day High", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    volume = _safe_numeric(rows.get("Volume", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    
    quantity = _safe_numeric(rows.get("Quantity", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    avg_price = _safe_numeric(rows.get("Average Price", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    buy_price_override = _safe_numeric(rows.get("Buy Price", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    
    buy_qty = quantity.copy()
    buy_price = buy_price_override.combine_first(avg_price)
    buy_date = pd.Series(rows.get("Buy Date", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    
    sell_date = pd.Series(rows.get("Sell Date", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    sell_price = _safe_numeric(rows.get("Sell Price", pd.Series([pd.NA] * len(rows), index=rows.index)), index=rows.index)
    
    # Read Side (LONG vs SHORT) and Status (OPEN vs CLOSED) if present in mock data
    side = rows.get("Side", pd.Series(["LONG"] * len(rows), index=rows.index)).astype(str).str.upper()
    status = rows.get("Status", pd.Series(["OPEN"] * len(rows), index=rows.index)).astype(str).str.upper()

    cmp = _safe_numeric(rows.get("CMP", 0))
    t_buy_price = buy_qty * buy_price

    # --- ADVANCED P&L & VALUATION ENGINE ---
    t_cmp_v = pd.Series(0.0, index=rows.index)
    profit = pd.Series(0.0, index=rows.index)

    for idx in rows.index:
        q = buy_qty.loc[idx]
        bp = buy_price.loc[idx]
        sp = sell_price.loc[idx]
        c = cmp.loc[idx]
        s = side.loc[idx]
        st_val = status.loc[idx]

        if pd.isna(q) or q == 0:
            continue

        if st_val == "CLOSED" and not pd.na(sp):
            # Closed Trade: Valuation and P&L locked to Exit Price
            t_cmp_v.loc[idx] = sp * q
            if s == "SHORT":
                profit.loc[idx] = (bp - sp) * q  # Sold high, bought back low = profit
            else:
                profit.loc[idx] = (sp - bp) * q  # Bought low, sold high = profit
        else:
            # Open Trade: Valuation driven by live CMP
            t_cmp_v.loc[idx] = c * q
            if s == "SHORT":
                profit.loc[idx] = (bp - c) * q   # Price drops = profit for short
            else:
                profit.loc[idx] = (c - bp) * q   # Price rises = profit for long

    t_sell_price = sell_price * buy_qty
    pct_profit = pd.Series([pd.NA] * len(rows), index=rows.index)
    valid_profit = t_buy_price.notna() & (t_buy_price != 0)
    pct_profit.loc[valid_profit] = (profit.loc[valid_profit] / t_buy_price.loc[valid_profit]) * 100

    output = pd.DataFrame({
        "Index": range(1, len(rows) + 1),
        "Stock Name": rows.get("Stock Name", pd.Series(["" for _ in range(len(rows))], index=rows.index)),
        "NSE/BSE etc": exchange,
        "EQ etc": "EQ",
        "CMP": cmp,
        "PC": pc,
        "Quantity": buy_qty,
        "Buy Date": buy_date,
        "Buy Price": buy_price,
        "Sell Date": sell_date,
        "Sell Price": sell_price,
        "T. Buy Price": t_buy_price,
        "T. Sell Price": t_sell_price,
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