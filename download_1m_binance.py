from binance.client import Client
import pandas as pd
import time

# Binance Futures client (public data ke liye API key zaruri nahi)
client = Client()

symbol = "BTCUSDT"
interval = Client.KLINE_INTERVAL_1MINUTE
limit = 1000  # ek request me max 1000 candles

# Data ko chunks me fetch karna (5 saal = ~2.6M candles)
def fetch_klines(symbol, interval, start_str, end_str=None):
    return client.futures_klines(
        symbol=symbol,
        interval=interval,
        start_str=start_str,
        end_str=end_str,
        limit=limit
    )

# Start date set kar (example 1 Jan 2020 se ab tak)
start_date = "1 Jan, 2020"
print("Downloading 1m futures data for BTCUSDT from", start_date)

all_data = []
last_time = start_date

while True:
    klines = fetch_klines(symbol, interval, last_time)
    if not klines:
        break
    all_data.extend(klines)
    last_time = klines[-1][0]  # last candle ka timestamp
    print("Fetched till:", pd.to_datetime(last_time, unit='ms'))
    time.sleep(0.5)  # Binance rate limit safe rakhne ke liye

    # Agar data ~ab tak aa gaya toh ruk jao
    if pd.to_datetime(last_time, unit='ms') >= pd.Timestamp.now():
        break

# Data ko DataFrame me convert
df = pd.DataFrame(all_data, columns=[
    "open_time","open","high","low","close","volume",
    "close_time","quote_asset_volume","num_trades",
    "taker_buy_base","taker_buy_quote","ignore"
])

# Correct types
df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
for col in ["open","high","low","close","volume"]:
    df[col] = df[col].astype(float)

# Save CSV
out_file = "binance_futures_BTCUSDT_1m.csv"
df.to_csv(out_file, index=False)
print("Saved:", out_file, "rows:", len(df))
