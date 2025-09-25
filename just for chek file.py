#!/usr/bin/env python3
"""
Fib 36.90% Strategy - LIVE/DEMO (MT5)
- Runs at 00:00, 05:30, 18:00 daily
- Places orders on MT5 (demo/live depending on login)
- Sends Telegram alerts per trade + daily summary
"""

import os
import sys
import csv
import time
import argparse
import datetime as dt
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd
import requests
import schedule

# ---------------- CONFIG ----------------
SYMBOL = "BTCUSDm"
INITIAL_BALANCE = 1000.0
RISK_PER_TRADE = 0.06
MOVE_PCT = 0.0059       # 0.59%
TRIGGER_LEVEL = 0.369   # 36.90%
TP_LEVEL = 1.111        # 111.10%

TRADE_HOURS = [(0,0), (5,30), (18,0)]
OUTDIR = Path("backtest_output")
OUTDIR.mkdir(exist_ok=True)
TRADES_CSV = OUTDIR / "trades_log.csv"
DAILY_SUMMARY = OUTDIR / "daily_summary.csv"

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------- HELPERS ----------------
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] ->", text); return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

def log_trade(row):
    header = ["datetime","side","entry","sl","tp","exit","pnl","reason"]
    write_header = not TRADES_CSV.exists()
    with open(TRADES_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if write_header: writer.writeheader()
        writer.writerow(row)

def append_summary(date_str, pnl):
    write_header = not DAILY_SUMMARY.exists()
    with open(DAILY_SUMMARY, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header: writer.writerow(["date","pnl"])
        writer.writerow([date_str, pnl])

def init_mt5():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    mt5.symbol_select(SYMBOL, True)

def shutdown_mt5():
    try: mt5.shutdown()
    except: pass

def get_data(count=500):
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, count)
    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    return df

def place_order(side, lots, sl, tp, live=True):
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None: raise RuntimeError("Tick not available")
    price = tick.ask if side=="BUY" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if side=="BUY" else mt5.ORDER_TYPE_SELL
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lots,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 100,
        "magic": 369369,
        "comment": "Fib369",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if live:
        return mt5.order_send(req)
    else:
        print("[SIM] order:", req)
        return {"retcode":"SIM_OK"}

# ---------------- STRATEGY ----------------
def run_strategy(live=True):
    df = get_data(200)
    now = dt.datetime.now()
    today = now.date()
    weekday = now.weekday()
    if weekday in (5,6): return  # skip weekend
    if not any((now.hour==h and now.minute==m) for h,m in TRADE_HOURS): return

    equity = INITIAL_BALANCE
    trades_today = pd.read_csv(TRADES_CSV)["datetime"].str[:10].eq(str(today)).sum() if TRADES_CSV.exists() else 0
    if trades_today>0: return

    start_price = df.iloc[-1]["close"]
    window = df.iloc[-50:]
    max_price, min_price = window["high"].max(), window["low"].min()

    # SELL setup
    if (max_price - start_price)/start_price >= MOVE_PCT:
        swing_low, swing_high = start_price, max_price
        fib_range = swing_high - swing_low
        trigger = swing_high - fib_range*TRIGGER_LEVEL
        tp = swing_high - fib_range*TP_LEVEL
        sl = swing_high*1.001
        entry = trigger
        risk = abs(sl-entry)
        lots = (equity*RISK_PER_TRADE)/risk if risk>0 else 0.01
        res = place_order("SELL", lots, sl, tp, live)
        send_telegram(f"📉 SELL placed {entry} sl={sl} tp={tp} lots={lots}")
        log_trade({"datetime":now,"side":"SELL","entry":entry,"sl":sl,"tp":tp,"exit":"","pnl":"","reason":"opened"})

    # BUY setup
    if (start_price - min_price)/start_price >= MOVE_PCT:
        swing_high, swing_low = start_price, min_price
        fib_range = swing_high - swing_low
        trigger = swing_low + fib_range*TRIGGER_LEVEL
        tp = swing_low + fib_range*TP_LEVEL
        sl = swing_low*0.999
        entry = trigger
        risk = abs(entry-sl)
        lots = (equity*RISK_PER_TRADE)/risk if risk>0 else 0.01
        res = place_order("BUY", lots, sl, tp, live)
        send_telegram(f"📈 BUY placed {entry} sl={sl} tp={tp} lots={lots}")
        log_trade({"datetime":now,"side":"BUY","entry":entry,"sl":sl,"tp":tp,"exit":"","pnl":"","reason":"opened"})

def daily_summary():
    today = dt.date.today().isoformat()
    pnl = 0.0
    if TRADES_CSV.exists():
        df = pd.read_csv(TRADES_CSV)
        df["date"] = pd.to_datetime(df["datetime"]).dt.date
        pnl = df[df["date"]==dt.date.today()]["pnl"].astype(float).sum()
    append_summary(today,pnl)
    send_telegram(f"📊 Daily Summary {today}: {pnl:.2f}")

# ---------------- MAIN ----------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Simulate orders only")
    args = parser.parse_args()
    live = not args.dry

    init_mt5()
    for h,m in TRADE_HOURS:
        schedule.every().day.at(f"{h:02d}:{m:02d}").do(run_strategy, live=live)
    schedule.every().day.at("23:59").do(daily_summary)

    send_telegram(f"🤖 Fib369 Live started (live={live}) times={TRADE_HOURS}")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_mt5()

if __name__=="__main__":
    main()
