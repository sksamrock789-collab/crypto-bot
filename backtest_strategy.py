#!/usr/bin/env python3
"""
Fib 36.90% Strategy Backtest (Auto Run, BUY + SELL + Breakdown)
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# ---------------- CONFIG ----------------
INPUT_CSV = "backtest_output/all_data.csv"
OUTDIR = Path("backtest_output")
OUTDIR.mkdir(exist_ok=True)

INITIAL_BALANCE = 1000.0
RISK_PER_TRADE = 0.06
MOVE_PCT = 0.0059     # 0.59% swing
TRIGGER_LEVEL = 0.369 # 36.90%
TP_LEVEL = 1.111      # 111.10%

TRADE_HOURS = [(0,0), (5,30), (18,0)]  # allowed times


# ---------------- HELPERS ----------------
def read_data(path):
    print(f"Loading data from: {path}")
    df = pd.read_csv(path)
    if "timestamp" in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    elif "time" in df.columns:
        df["datetime"] = pd.to_datetime(df["time"], unit="s")
    else:
        raise ValueError("CSV must contain timestamp or time column")
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def compute_drawdown(equity_curve):
    roll_max = equity_curve.cummax()
    dd = (equity_curve - roll_max) / roll_max
    return dd, dd.min()


# ---------------- STRATEGY ----------------
def run_backtest(df):
    equity = INITIAL_BALANCE
    trades = []
    last_tp_day = None

    for i in range(100, len(df)):
        row = df.iloc[i]
        dt_row = row["datetime"]

        # weekend skip
        if dt_row.weekday() in (5,6):
            continue

        # time filter
        if not any((dt_row.hour == h and dt_row.minute == m) for h,m in TRADE_HOURS):
            continue

        # daily TP skip
        if last_tp_day == dt_row.date():
            continue

        start_price = row["close"]
        window = df.iloc[i:i+50]
        if window.empty:
            continue

        max_price = window["high"].max()
        min_price = window["low"].min()

        # -------- SELL setup (UP move) --------
        if (max_price - start_price) / start_price >= MOVE_PCT:
            swing_low = start_price
            swing_high = max_price
            fib_range = swing_high - swing_low

            trigger_price = swing_high - fib_range * TRIGGER_LEVEL
            tp_price = swing_high - fib_range * TP_LEVEL
            sl_price = swing_high * 1.001

            armed = False
            for j in range(i+1, len(df)):
                r = df.iloc[j]
                if r["datetime"].date() != dt_row.date():
                    break

                if not armed and r["close"] < trigger_price:
                    armed = True
                    continue

                if armed and r["high"] >= trigger_price:
                    entry = trigger_price
                    lots = (equity * RISK_PER_TRADE) / max(sl_price - entry, 1e-6)

                    outcome, exit_price, exit_time = None, None, None
                    for k in range(j, len(df)):
                        r2 = df.iloc[k]
                        if r2["datetime"].date() != dt_row.date():
                            break
                        if r2["low"] <= tp_price:
                            outcome, exit_price, exit_time = "TP", tp_price, r2["datetime"]; break
                        if r2["high"] >= sl_price:
                            outcome, exit_price, exit_time = "SL", sl_price, r2["datetime"]; break

                    if outcome is None:
                        r2 = df.iloc[j]
                        outcome, exit_price, exit_time = "EOD", r2["close"], r2["datetime"]

                    pnl = (entry - exit_price) * lots
                    equity += pnl
                    trades.append({
                        "side": "SELL",
                        "entry_time": r["datetime"],
                        "entry": entry,
                        "sl": sl_price,
                        "tp": tp_price,
                        "exit_time": exit_time,
                        "exit": exit_price,
                        "pnl": pnl,
                        "reason": outcome
                    })

                    if outcome == "TP":
                        last_tp_day = dt_row.date()
                    break

        # -------- BUY setup (DOWN move) --------
        if (start_price - min_price) / start_price >= MOVE_PCT:
            swing_high = start_price
            swing_low = min_price
            fib_range = swing_high - swing_low

            trigger_price = swing_low + fib_range * TRIGGER_LEVEL
            tp_price = swing_low + fib_range * TP_LEVEL
            sl_price = swing_low * 0.999

            armed = False
            for j in range(i+1, len(df)):
                r = df.iloc[j]
                if r["datetime"].date() != dt_row.date():
                    break

                if not armed and r["close"] > trigger_price:
                    armed = True
                    continue

                if armed and r["low"] <= trigger_price:
                    entry = trigger_price
                    lots = (equity * RISK_PER_TRADE) / max(entry - sl_price, 1e-6)

                    outcome, exit_price, exit_time = None, None, None
                    for k in range(j, len(df)):
                        r2 = df.iloc[k]
                        if r2["datetime"].date() != dt_row.date():
                            break
                        if r2["high"] >= tp_price:
                            outcome, exit_price, exit_time = "TP", tp_price, r2["datetime"]; break
                        if r2["low"] <= sl_price:
                            outcome, exit_price, exit_time = "SL", sl_price, r2["datetime"]; break

                    if outcome is None:
                        r2 = df.iloc[j]
                        outcome, exit_price, exit_time = "EOD", r2["close"], r2["datetime"]

                    pnl = (exit_price - entry) * lots
                    equity += pnl
                    trades.append({
                        "side": "BUY",
                        "entry_time": r["datetime"],
                        "entry": entry,
                        "sl": sl_price,
                        "tp": tp_price,
                        "exit_time": exit_time,
                        "exit": exit_price,
                        "pnl": pnl,
                        "reason": outcome
                    })

                    if outcome == "TP":
                        last_tp_day = dt_row.date()
                    break

    return pd.DataFrame(trades)


# ---------------- REPORTS ----------------
def generate_reports(trades):
    """
    Generate summary, monthly pnl, equity curve, drawdown, charts and save
    everything into backtest_output/backtest_report.xlsx and PNG files.
    """
    trades = trades.copy()
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])

    # equity curve
    equity_curve = INITIAL_BALANCE + trades["pnl"].cumsum()
    equity_curve = pd.Series(equity_curve.values, index=np.arange(len(equity_curve)), name="equity")

    # drawdown
    dd, max_dd = compute_drawdown(equity_curve)

    # overall stats
    wins = (trades["pnl"] > 0).sum()
    losses = (trades["pnl"] <= 0).sum()
    total = len(trades)
    winrate = (wins / total * 100) if total > 0 else 0.0

    print("\n===== Backtest Summary =====")
    print(f"Initial Equity: {INITIAL_BALANCE:.2f}")
    print(f"Final Equity: {equity_curve.iloc[-1]:.2f}")
    print(f"Net PnL: {equity_curve.iloc[-1] - INITIAL_BALANCE:.2f}")
    print(f"Total Trades: {total}")
    print(f"Wins: {wins} | Losses: {losses} | Winrate: {winrate:.2f}%")
    print(f"Max Drawdown: {max_dd:.2%}")

    # buy/sell breakdown
    for side in ["BUY", "SELL"]:
        side_trades = trades[trades["side"] == side]
        if not side_trades.empty:
            swins = (side_trades["pnl"] > 0).sum()
            sloss = (side_trades["pnl"] <= 0).sum()
            stotal = len(side_trades)
            swinrate = swins / stotal * 100
            print(f"\n-- {side} Breakdown --")
            print(f"Trades: {stotal} | Wins: {swins} | Losses: {sloss} | Winrate: {swinrate:.2f}%")

    # monthly pnl
    trades["month"] = trades["exit_time"].dt.to_period("M")
    monthly = trades.groupby("month")["pnl"].sum()

    # --- Save Excel report with multiple sheets ---
    report_path = OUTDIR / "backtest_report.xlsx"
    try:
        with pd.ExcelWriter(report_path, engine="xlsxwriter") as writer:
            # Trades (raw)
            trades.to_excel(writer, sheet_name="Trades", index=False)

            # Monthly PnL
            monthly_df = monthly.reset_index()
            monthly_df["month"] = monthly_df["month"].astype(str)
            monthly_df.to_excel(writer, sheet_name="Monthly_PnL", index=False)

            # Equity curve
            equity_df = pd.DataFrame({"equity": equity_curve.values})
            equity_df.to_excel(writer, sheet_name="Equity_Curve", index=False)

            # Drawdown
            dd_df = pd.DataFrame({"drawdown": dd.values})
            dd_df.to_excel(writer, sheet_name="Drawdown", index=False)

            # Summary
            summary = pd.DataFrame({
                "Initial Balance": [INITIAL_BALANCE],
                "Final Balance": [equity_curve.iloc[-1]],
                "Net PnL": [equity_curve.iloc[-1] - INITIAL_BALANCE],
                "Total Trades": [total],
                "Wins": [wins],
                "Losses": [losses],
                "Winrate %": [winrate],
                "Max Drawdown %": [max_dd * 100]
            })
            summary.to_excel(writer, sheet_name="Summary", index=False)

        print(f"✅ Report saved to {report_path}")
    except Exception as e:
        print("Error saving Excel report:", e)

    # --- Save charts ---
    try:
        plt.figure(figsize=(12, 6))
        plt.plot(equity_curve.values)
        plt.title("Equity Curve")
        plt.xlabel("Trade #")
        plt.ylabel("Equity")
        plt.grid(True)
        plt.savefig(OUTDIR / "equity_curve.png")
        plt.close()

        plt.figure(figsize=(12, 4))
        plt.plot(dd.values)
        plt.title("Drawdown")
        plt.xlabel("Trade #")
        plt.ylabel("Drawdown")
        plt.grid(True)
        plt.savefig(OUTDIR / "drawdown.png")
        plt.close()

        print(f"✅ Charts saved to {OUTDIR}/equity_curve.png and drawdown.png")
    except Exception as e:
        print("Error saving charts:", e)


# ---------------- MAIN ----------------
def main():
    df = read_data(INPUT_CSV)
    trades = run_backtest(df)
    if trades.empty:
        print("No trades executed.")
        return
    generate_reports(trades)

if __name__ == "__main__":
    main()
