# btcusdt_backtest.py
"""
Full improved backtest for BTC strategy:
 - EMA trend filter (50/200)
 - Fibonacci retrace touches (0.5, 0.618)
 - RSI filter (14)
 - ATR-based SL buffer + RR TP
 - Risk sizing by % equity
 - Trailing stop (simple)
 - Daily limits & daily loss stop
 - Outputs: trades_log.csv, equity_curve.png, monthly_report.csv
"""

import sys
import os
import math
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings("ignore")

# ---- CONFIG ----
RISK_PER_TRADE = 0.06         # 6% of equity
RR = 1.8
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
EMA_FAST = 50
EMA_SLOW = 200
ATR_PERIOD = 14
FIB_LEVELS = [0.618, 0.5]
FIB_TOL_PCT = 0.002           # 0.2% tolerance for touch
SWING_LOOKBACK = 200          # bars to find swing high/low
DAILY_MAX_TRADES = 5
DAILY_MAX_LOSS_PCT = 0.10     # stop trading for that day if equity loses 10%
MIN_LOT = 0.001               # not used for crypto backtest but kept
LOT_STEP = 0.001

# Time filter (optional): list of allowed hours (UTC). Empty => always allow
# Example: trade only 10-16 UTC => allowed_hours = list(range(10,17))
ALLOWED_HOURS = []  # [] => all day

# Output files
TRADES_CSV = "trades_log.csv"
EQUITY_PNG = "equity_curve.png"
MONTHLY_CSV = "monthly_report.csv"

# ---- Helpers / Indicators ----
def load_csv(path):
    df = pd.read_csv(path)
    # Guess timestamp column
    if "timestamp" in df.columns:
        # epoch ms or s? detect
        ts = df["timestamp"].iloc[0]
        if ts > 1e12:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        elif ts > 1e9:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        else:
            # fallback: try direct parse
            df["datetime"] = pd.to_datetime(df["timestamp"])
    elif "open_time" in df.columns:
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", errors="coerce")
    elif "close_time" in df.columns:
        df["datetime"] = pd.to_datetime(df["close_time"], unit="ms", errors="coerce")
    elif "date" in df.columns or "datetime" in df.columns:
        key = "datetime" if "datetime" in df.columns else "date"
        df["datetime"] = pd.to_datetime(df[key])
    else:
        raise RuntimeError("Could not find timestamp column (timestamp/open_time/close_time/datetime/date)")

    # Normalize OHLC column names
    colmap = {}
    for c in df.columns:
        low = c.lower()
        if low in ("open","open_price"): colmap[c] = "open"
        if low in ("high","high_price"): colmap[c] = "high"
        if low in ("low","low_price"): colmap[c] = "low"
        if low in ("close","close_price","close_time_close"): colmap[c] = "close"
        if "volume" in low and "quote" not in low:
            colmap[c] = "volume"
    df = df.rename(columns=colmap)
    required = ["open","high","low","close","datetime"]
    for r in required:
        if r not in df.columns:
            raise RuntimeError(f"Required column '{r}' not found after mapping. Available cols: {df.columns.tolist()}")
    df = df[["datetime","open","high","low","close"] + ([c for c in ("volume",) if c in df.columns])]
    df = df.sort_values("datetime").reset_index(drop=True)
    return df

def add_indicators(df):
    df = df.copy()
    df["ema50"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(window=RSI_PERIOD).mean()
    loss = -delta.clip(upper=0).rolling(window=RSI_PERIOD).mean()
    rs = gain / (loss.replace(0, np.nan))
    df["rsi"] = 100 - (100 / (1 + rs))
    # ATR
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["close"].shift(1)).abs()
    df["tr3"] = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = df[["tr1","tr2","tr3"]].max(axis=1)
    df["atr"] = df["tr"].rolling(window=ATR_PERIOD).mean()
    df.drop(columns=["tr1","tr2","tr3","tr"], inplace=True)
    return df

def find_recent_swings(df, idx, lookback=SWING_LOOKBACK):
    start = max(0, idx - lookback)
    sub = df.iloc[start: idx+1]
    if sub.empty:
        return None, None, None, None
    hi_idx = sub["high"].idxmax()
    lo_idx = sub["low"].idxmin()
    swing_high = float(df.at[hi_idx, "high"])
    swing_low = float(df.at[lo_idx, "low"])
    return swing_high, int(hi_idx), swing_low, int(lo_idx)

def compute_fib_levels(swing_high, swing_low):
    diff = swing_high - swing_low
    return {lvl: swing_high - diff * lvl for lvl in FIB_LEVELS}

def touched_level(price, level_val):
    tol = level_val * FIB_TOL_PCT
    return abs(price - level_val) <= tol

def calc_lots_from_risk(equity, entry, sl):
    risk_amount = equity * RISK_PER_TRADE
    price_diff = abs(entry - sl)
    if price_diff <= 0:
        return 0.001
    # assume 1 contract per lot (crypto)
    lots = risk_amount / price_diff
    # round to LOT_STEP
    steps = math.floor(lots / LOT_STEP)
    lots_adj = max(LOT_STEP, steps * LOT_STEP)
    return round(lots_adj, 6)

# ---- Backtest engine ----
def run_backtest(df, initial_equity=10000):
    equity = initial_equity
    equity_curve = []
    trades = []
    daily_trades = {}
    daily_loss_stop = {}
    last_trade_day = None

    for idx in range(len(df)):
        row = df.iloc[idx]
        dtstamp = row["datetime"]
        price = float(row["close"])
        if idx < ATR_PERIOD or idx < EMA_SLOW:
            equity_curve.append((dtstamp, equity))
            continue

        # time filter
        if ALLOWED_HOURS:
            if dtstamp.hour not in ALLOWED_HOURS:
                equity_curve.append((dtstamp, equity))
                continue

        # daily reset
        day = dtstamp.date()
        if day != last_trade_day:
            daily_trades[day] = 0
            daily_loss_stop[day] = False
            last_trade_day = day

        if daily_loss_stop.get(day, False):
            equity_curve.append((dtstamp, equity))
            continue

        # skip if daily max trades reached
        if daily_trades.get(day, 0) >= DAILY_MAX_TRADES:
            equity_curve.append((dtstamp, equity))
            continue

        # indicators at this bar
        rsi = row["rsi"]
        ema50 = row["ema50"]
        ema200 = row["ema200"]
        atr = row["atr"]

        # find swings and fibs
        swing_high, shi, swing_low, sli = find_recent_swings(df, idx)
        if swing_high is None:
            equity_curve.append((dtstamp, equity))
            continue
        fibs = compute_fib_levels(swing_high, swing_low)

        # check fib touches
        signal = None
        for lvl, lvl_val in fibs.items():
            if touched_level(price, lvl_val):
                # BUY condition
                if rsi is not None and rsi <= RSI_OVERSOLD and ema50 > ema200:
                    side = "BUY"
                    entry = price
                    sl = swing_low - (0.5 * atr)   # buffer 0.5 ATR
                    tp = entry + (entry - sl) * RR
                    signal = dict(side=side, entry=entry, sl=sl, tp=tp, level=lvl)
                    break
                # SELL condition
                if rsi is not None and rsi >= RSI_OVERBOUGHT and ema50 < ema200:
                    side = "SELL"
                    entry = price
                    sl = swing_high + (0.5 * atr)
                    tp = entry - (sl - entry) * RR
                    signal = dict(side=side, entry=entry, sl=sl, tp=tp, level=lvl)
                    break

        if not signal:
            equity_curve.append((dtstamp, equity))
            continue

        # position sizing
        lots = calc_lots_from_risk(equity, signal["entry"], signal["sl"])
        if lots <= 0:
            equity_curve.append((dtstamp, equity))
            continue

        # Now simulate trade result: find next bars where SL or TP hit
        entry_price = signal["entry"]
        target_sl = signal["sl"]
        target_tp = signal["tp"]
        side = signal["side"]

        # simulate forward until hit sl or tp or horizon (max 1000 bars)
        exit_price = None
        exit_time = None
        exit_reason = None
        pnl = 0.0
        trailing_active = False
        trailing_sl = target_sl

        for j in range(idx+1, min(len(df), idx + 1000)):
            r = df.iloc[j]
            h = float(r["high"])
            l = float(r["low"])
            ttime = r["datetime"]

            # SELL: tp < entry, sl > entry
            if side == "BUY":
                # TP hit?
                if l <= target_tp:
                    exit_price = target_tp
                    exit_time = ttime
                    exit_reason = "TP"
                    pnl = (exit_price - entry_price) * lots
                    break
                # SL hit?
                if h >= target_sl and target_sl > entry_price:
                    # unusual but check
                    exit_price = target_sl
                    exit_time = ttime
                    exit_reason = "SL"
                    pnl = (exit_price - entry_price) * lots
                    break
                if l <= target_sl:
                    exit_price = target_sl
                    exit_time = ttime
                    exit_reason = "SL"
                    pnl = (exit_price - entry_price) * lots
                    break
            else:  # SELL
                if h >= target_tp:
                    exit_price = target_tp
                    exit_time = ttime
                    exit_reason = "TP"
                    pnl = (entry_price - exit_price) * lots
                    break
                if l <= target_sl:
                    exit_price = target_sl
                    exit_time = ttime
                    exit_reason = "SL"
                    pnl = (entry_price - exit_price) * lots
                    break

            # trailing stop simple: if unrealized profit > 1x initial risk, move SL to breakeven
            unreal = ( (r["close"] - entry_price) * lots ) if side == "BUY" else ((entry_price - r["close"]) * lots)
            initial_risk = abs(entry_price - target_sl) * lots
            if not trailing_active and unreal >= initial_risk:
                trailing_active = True
                # move SL to breakeven (entry)
                if side == "BUY":
                    target_sl = entry_price
                else:
                    target_sl = entry_price

        # if no exit found within horizon, close at last close
        if exit_price is None:
            last = df.iloc[min(len(df)-1, idx+999)]
            exit_price = float(last["close"])
            exit_time = last["datetime"]
            exit_reason = "NO_HIT"
            pnl = (exit_price - entry_price) * lots if side=="BUY" else (entry_price - exit_price) * lots

        # update equity
        equity += pnl
        equity_curve.append((exit_time, equity))

        # log trade
        trades.append({
            "entry_time": row["datetime"],
            "exit_time": exit_time,
            "side": side,
            "entry": entry_price,
            "sl": target_sl,
            "tp": target_tp,
            "lots": lots,
            "pnl": pnl,
            "reason": exit_reason
        })

        # daily bookkeeping
        daily_trades[day] = daily_trades.get(day, 0) + 1
        # if daily loss exceeded, mark stop
        day_pnl = sum([t["pnl"] for t in trades if pd.to_datetime(t["entry_time"]).date() == day])
        if day_pnl <= -initial_equity * DAILY_MAX_LOSS_PCT:
            daily_loss_stop[day] = True

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_curve, columns=["datetime","equity"]).dropna().reset_index(drop=True)
    return trades_df, equity_df

# ---- Metrics and monthly report ----
def compute_metrics(trades_df, equity_df, initial_equity):
    total_trades = len(trades_df)
    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] <= 0]
    winrate = len(wins) / total_trades * 100 if total_trades>0 else 0
    gross_win = wins["pnl"].sum()
    gross_loss = -losses["pnl"].sum()
    profit_factor = gross_win / gross_loss if gross_loss>0 else np.inf
    net_profit = trades_df["pnl"].sum()
    # max drawdown from equity series
    eq = equity_df["equity"].values if not equity_df.empty else np.array([initial_equity])
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq).max()
    # CAGR (approx)
    days = (equity_df["datetime"].max() - equity_df["datetime"].min()).days if not equity_df.empty else 1
    years = max(1/365, days/365)
    ending = eq[-1] if len(eq)>0 else initial_equity
    cagr = (ending / initial_equity) ** (1/years) - 1
    return {
        "total_trades": total_trades,
        "winrate": winrate,
        "profit_factor": profit_factor,
        "net_profit": net_profit,
        "max_drawdown": dd,
        "cagr": cagr
    }

def monthly_report(trades_df):
    if trades_df.empty:
        return pd.DataFrame()
    tr = trades_df.copy()
    tr["entry_time"] = pd.to_datetime(tr["entry_time"])
    tr["month"] = tr["entry_time"].dt.to_period("M").astype(str)
    grouped = tr.groupby("month").agg(
        trades = ("pnl","count"),
        net_pnl = ("pnl","sum"),
        wins = ("pnl", lambda x: (x>0).sum()),
        losses = ("pnl", lambda x: (x<=0).sum()),
    ).reset_index()
    grouped["winrate"] = grouped["wins"] / grouped["trades"] * 100
    return grouped

# ---- Main CLI ----
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", help="Path to historical csv (merged 5y BTC candles)")
    parser.add_argument("--initial", type=float, default=10000, help="Initial equity")
    args = parser.parse_args()

    df = load_csv(args.csv)
    print("Loaded", len(df), "rows, head:")
    print(df.head())

    df = add_indicators(df)
    trades_df, equity_df = run_backtest(df, initial_equity=args.initial)

    # save trades
    trades_df.to_csv(TRADES_CSV, index=False)
    print(f"Trades saved to {TRADES_CSV} ({len(trades_df)} trades)")

    # equity plot
    if not equity_df.empty:
        plt.figure(figsize=(12,6))
        plt.plot(equity_df["datetime"], equity_df["equity"], label="Equity")
        plt.xlabel("Time"); plt.ylabel("Equity")
        plt.title("Equity Curve")
        plt.grid(True); plt.legend()
        plt.tight_layout()
        plt.savefig(EQUITY_PNG)
        print(f"Equity curve saved to {EQUITY_PNG}")

    # monthly
    monthly = monthly_report(trades_df)
    monthly.to_csv(MONTHLY_CSV, index=False)
    print(f"Monthly report saved to {MONTHLY_CSV}")

    # summary metrics
    metrics = compute_metrics(trades_df, equity_df, args.initial)
    print("Summary metrics:", metrics)

if __name__ == "__main__":
    main()
