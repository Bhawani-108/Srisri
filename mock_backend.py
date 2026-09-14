import time
import random
import pandas as pd

CSV_FILE = "portfolio.csv"

def simulate_market_feed():
    try:
        # Read the CSV sheet
        df = pd.read_csv(CSV_FILE)
        
        # FIX: Force the column to float to accept decimal prices
        df['Live Price'] = df['Live Price'].astype(float)
        
        # Update ONLY the Live Price column row by row
        for index, row in df.iterrows():
            current_price = row['Live Price']
            
            # If price is somehow 0 or empty, baseline it to Average Price
            if current_price == 0 or pd.isna(current_price):
                current_price = float(row['Average Price'])
            
            # Change price slightly (-1.5% to +1.5%)
            change_percent = random.uniform(-0.015, 0.015) 
            new_price = round(current_price * (1 + change_percent), 2)
            
            # Explicitly target only the 'Live Price' cell
            df.loc[index, 'Live Price'] = new_price
        
        # Save back to CSV cleanly
        df.to_csv(CSV_FILE, index=False)
        print(f"[{time.strftime('%H:%M:%S')}] Simulated live price feed update saved to CSV.")
        
    except PermissionError:
        print("⚠️ Warning: File locked. Retrying next loop...")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("🚀 Starting Mock Market Data Engine (Ctrl+C to stop)...")
    try:
        while True:
            simulate_market_feed()
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nMock Market Engine stopped.")