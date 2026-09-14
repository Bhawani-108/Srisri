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


def _safe_numeric(series):
    result = pd.to_numeric(series, errors="coerce")
    if isinstance(result, pd.Series):
        return result
    return pd.Series([result] * len(df), index=df.index) if hasattr(df, 'index') else pd.Series([result])


def build_ordered_display_frame(df, row_type="Holding"):
    if df is None or df.empty:
        return pd.DataFrame(columns=ORDERED_COLUMNS)

    rows = df.copy()
    rows = rows.reset_index(drop=True)

    is_watchlist = str(row_type).lower() == "watchlist" or (
        "Type" in rows.columns and rows["Type"].astype(str).str.lower().eq("watchlist").any()
    )

    exchange = rows.get("Exchange", pd.Series(["NSE"] * len(rows), index=rows.index))
    exchange = exchange.fillna("NSE").astype(str)

    cmp = _safe_numeric(rows.get("CMP", pd.Series([pd.NA] * len(rows), index=rows.index)))
    pc = _safe_numeric(rows.get("PC", pd.Series([pd.NA] * len(rows), index=rows.index)))
    day_high = _safe_numeric(rows.get("Day High", pd.Series([pd.NA] * len(rows), index=rows.index)))
    volume = _safe_numeric(rows.get("Volume", pd.Series([pd.NA] * len(rows), index=rows.index)))
    quantity = _safe_numeric(rows.get("Quantity", pd.Series([pd.NA] * len(rows), index=rows.index)))
    avg_price = _safe_numeric(rows.get("Average Price", pd.Series([pd.NA] * len(rows), index=rows.index)))
    buy_price_override = _safe_numeric(rows.get("Buy Price", pd.Series([pd.NA] * len(rows), index=rows.index)))
    recorded_buy_date = rows.get("Buy Date", pd.Series([pd.NA] * len(rows), index=rows.index))
    d_pct = _safe_numeric(rows.get("D%", pd.Series([pd.NA] * len(rows), index=rows.index)))
    dh_pct = _safe_numeric(rows.get("DH%", pd.Series([pd.NA] * len(rows), index=rows.index)))
    s_alert = _safe_numeric(rows.get("SAlert", pd.Series([pd.NA] * len(rows), index=rows.index)))

    buy_qty = quantity.copy()
    buy_price = buy_price_override.combine_first(avg_price)
    buy_date = pd.Series(recorded_buy_date, index=rows.index)
    sell_date = pd.Series([pd.NA] * len(rows), index=rows.index)
    sell_price = pd.Series([pd.NA] * len(rows), index=rows.index)

    if is_watchlist:
        buy_qty = pd.Series([pd.NA] * len(rows), index=rows.index)
        buy_price = pd.Series([pd.NA] * len(rows), index=rows.index)
        buy_date = pd.Series([pd.NA] * len(rows), index=rows.index)

    t_buy_price = buy_qty * buy_price
    t_sell_price = sell_price * buy_qty
    t_cmp_v = cmp * buy_qty
    profit = t_cmp_v - t_buy_price

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

    for col in output.columns:
        output[col] = output[col].where(output[col].notna(), "NA")

    return output
