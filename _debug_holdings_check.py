import pandas as pd
from views.demat_display import build_demat_display_frame

df = pd.DataFrame([
    {
        "Stock Name": "INFY",
        "Exchange": "NSE",
        "Token": "123",
        "Type": "Holding",
        "CMP": 100,
        "PC": 90,
        "Day High": 95,
        "Volume": 1000,
        "Quantity": 10,
        "Average Price": 80,
        "Broker": "angel_one",
        "1D%": 1.5,
        "2D%": 2.0,
        "3D%": -1.0,
        "4D%": 0.5,
        "PMC": 85,
        "PD Volume": 800,
    }
])
out = build_demat_display_frame(df)
print(out.columns.tolist())
print(out[["1D%", "2D%", "3D%", "4D%"]].to_dict("list"))
