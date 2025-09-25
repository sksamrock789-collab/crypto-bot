import os
import requests

# Binance Futures UM perpetual data base URL
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/5m/"

# Local folder to save files
SAVE_DIR = "binance_futures_5years"
os.makedirs(SAVE_DIR, exist_ok=True)

# Years aur months (2019–2024 = 5 years)
years = [2019, 2020, 2021, 2022, 2023, 2024]
months = [f"{i:02d}" for i in range(1, 13)]

for year in years:
    for month in months:
        filename = f"BTCUSDT-5m-{year}-{month}.zip"
        url = BASE_URL + filename
        save_path = os.path.join(SAVE_DIR, filename)

        # Agar file already downloaded hai toh skip kare
        if os.path.exists(save_path):
            print(f"Already downloaded: {filename}")
            continue

        try:
            print(f"Downloading {filename} ...")
            response = requests.get(url, stream=True, timeout=60)
            if response.status_code == 200:
                with open(save_path, "wb") as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                print(f"✅ Saved: {save_path}")
            else:
                print(f"❌ Not available: {filename}")
        except Exception as e:
            print(f"⚠️ Error downloading {filename}: {e}")

print("🎉 All downloads complete for 5 years BTCUSDT Futures 5m data!")
