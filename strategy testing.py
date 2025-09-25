"""
strategy_report.py
Generates monthly backtest report, charts, and (optional) Telegram summary
Assumes trades log at backtest_output/trades_log.csv with columns:
  date, time, direction, entry, sl, tp, lots, profit, comment
"""

import os
import sys
import math
import datetime as dt
import pandas as pd
import matplotlib.pyplot as plt

# ------------------ CONFIG ------------------
OUT_FOLDER = "backtest_output"
TRADES_CSV = os.path.join(OUT_FOLDER, "trades_log.csv")
MONTHLY_CSV = os.path.join(OUT_FOLDER, "monthly_report.csv")
EQ_PLOT = os.path.join(OUT_FOLDER, "equity.png")
MONTHLY_PNL_PLOT = os.path.join(OUT_FOLDER, "monthly_pnl.png")
DRAWDOWN_PLOT = os.path.join(OUT_FOLDER, "drawdown.png")

# Telegram (optional) - set these if you want telegram notifications
TELEGRAM_ENABLED = True
TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"
CHAT_ID = "PUT_YOUR_CHAT_ID_HERE"

# ------------------ HELPERS ------------------
def send_message(text: str):
    if not TELEGRAM_ENABLED or TOKEN.startswith("PUT_"):
        print("[telegram disabled] message:\n", text)
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

def ensure_out_folder():
    if not os.path.exists(OUT_FOLDER):
        os.makedirs(OUT_FOLDER, exist_ok=True)

def read_trades(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Trades file not found: {path}")
    df = pd.read_csv(path)
    # normalize column names
    df.columns = [c.strip() for c in df.columns]
    # expect at least 'date' and 'profit' columns
    if "date" not in df.columns and "time" not in df.columns:
        raise ValueError("Trades CSV missing required 'date' or 'time' columns.")
    # combine date+time if available
    if "time" in df.columns:
        df["datetime"] = df["date"].astype(str) + " " + df["time"].astype(str)
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce", utc=False)
    else:
        df["datetime"] = pd.to_datetime(df["date"], errors="coerce")
    if "profit" not in df.columns:
        # maybe profit column name is 'profit' but typed different: try lower
        lowcols = [c.lower() for c in df.columns]
        if "profit" in lowcols:
            idx = lowcols.index("profit")
            df["profit"] = df.iloc[:, idx]
        else:
            raise ValueError("Trades CSV missing 'profit' column — cannot compute PnL.")
    df["profit"] = pd.to_numeric(df["profit"], errors="coerce").fillna(0.0)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df

def compute_equity_series(trades_df, initial_balance=0.0):
    # Each trade increases equity by profit. We'll construct equity series at trade timestamps.
    df = trades_df.copy()
    df["cum_pnl"] = df["profit"].cumsum() + initial_balance
    equity_df = df[["datetime", "cum_pnl"]].rename(columns={"cum_pnl": "equity"})
    return equity_df

def max_drawdown_from_series(equity_series):
    """equity_series -> pd.Series sorted by time"""
    roll_max = equity_series.cummax()
    drawdown = (equity_series - roll_max) / roll_max.replace(0, pd.NA)
    # return maximum drawdown as positive percent
    if drawdown.dropna().empty:
        return 0.0
    max_dd = drawdown.min()
    return float(abs(max_dd)) * 100.0

def monthly_drawdown(trades_df):
    # For each month, compute drawdown (based on cumulative equity within that month)
    if trades_df.empty:
        return {}
    trades = trades_df.copy()
    trades["month"] = trades["datetime"].dt.to_period("M").astype(str)
    monthly_dd = {}
    for m, grp in trades.groupby("month"):
        eq = grp["profit"].cumsum()
        dd = max_drawdown_from_series(eq)
        monthly_dd[m] = dd
    return monthly_dd

# ------------------ MAIN REPORT ------------------
def generate_report(trades_csv=TRADES_CSV, initial_balance=0.0):
    ensure_out_folder()
    try:
        trades = read_trades(trades_csv)
    except Exception as e:
        print("❌ Error reading trades:", e)
        print("Make sure trades_log.csv exists in", OUT_FOLDER)
        sys.exit(1)

    print(f"Loading data from: {trades_csv}")
    print(f"Data loaded. Rows:", len(trades))

    # basic trade stats
    trades["month"] = trades["datetime"].dt.to_period("M").astype(str)
    trades["is_win"] = trades["profit"] > 0
    trades["is_loss"] = trades["profit"] < 0

    # monthly aggregation
    monthly = trades.groupby("month").agg(
        trades_count = pd.NamedAgg(column="profit", aggfunc="count"),
        wins = pd.NamedAgg(column="is_win", aggfunc="sum"),
        losses = pd.NamedAgg(column="is_loss", aggfunc="sum"),
        total_pnl = pd.NamedAgg(column="profit", aggfunc="sum"),
    ).reset_index()
    monthly["winrate_pct"] = (monthly["wins"] / monthly["trades_count"] * 100).round(2).fillna(0.0)
    monthly["avg_pnl_per_trade"] = (monthly["total_pnl"] / monthly["trades_count"]).round(2)
    # monthly drawdown
    mdd = monthly_drawdown(trades)
    monthly["max_drawdown_pct"] = monthly["month"].map(lambda m: round(mdd.get(m, 0.0), 2))

    # save monthly CSV
    monthly.to_csv(MONTHLY_CSV, index=False)
    print(f"✅ Monthly report saved: {MONTHLY_CSV}")

    # overall equity series
    equity_df = compute_equity_series(trades, initial_balance=initial_balance)
    # save copy of all trades too
    trades.to_csv(os.path.join(OUT_FOLDER, "trades_log_clean.csv"), index=False)

    # overall statistics
    overall_max_dd = max_drawdown_from_series(equity_df["equity"])
    total_trades = len(trades)
    total_pnl = trades["profit"].sum()
    wins = trades["is_win"].sum()
    losses = trades["is_loss"].sum()

    print("----- Summary -----")
    print("Trades executed:", total_trades)
    print("Total PnL:", round(total_pnl,2))
    print("Wins:", int(wins), "Losses:", int(losses))
    print(f"Max Drawdown: {overall_max_dd:.2f}%")

    # ----------------- Charts -----------------
    # 1) Equity curve
    try:
        plt.figure(figsize=(10,5))
        plt.plot(equity_df["datetime"], equity_df["equity"], marker="", linewidth=1)
        plt.title("Equity Curve")
        plt.xlabel("Time")
        plt.ylabel("Equity (cum. PnL + initial)")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(EQ_PLOT)
        plt.close()
        print("✅ Equity plot saved:", EQ_PLOT)
    except Exception as e:
        print("⚠️ Equity plot error:", e)

    # 2) Monthly PnL bar chart
    try:
        monthly_sorted = monthly.sort_values("month")
        plt.figure(figsize=(10,5))
        bars = plt.bar(monthly_sorted["month"], monthly_sorted["total_pnl"])
        # color bars
        for bar, val in zip(bars, monthly_sorted["total_pnl"]):
            bar.set_color("green" if val>=0 else "red")
        plt.xticks(rotation=45, ha="right")
        plt.title("Monthly PnL")
        plt.ylabel("Total PnL")
        plt.tight_layout()
        plt.savefig(MONTHLY_PNL_PLOT)
        plt.close()
        print("✅ Monthly PnL plot saved:", MONTHLY_PNL_PLOT)
    except Exception as e:
        print("⚠️ Monthly PnL plot error:", e)

    # 3) Drawdown plot (rolling)
    try:
        eq = equity_df.set_index("datetime")["equity"]
        running_max = eq.cummax()
        dd = (eq - running_max) / running_max.replace(0, pd.NA)
        plt.figure(figsize=(10,4))
        plt.plot(dd.index, dd * 100)
        plt.title("Drawdown (%)")
        plt.ylabel("Drawdown %")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(DRAWDOWN_PLOT)
        plt.close()
        print("✅ Drawdown plot saved:", DRAWDOWN_PLOT)
    except Exception as e:
        print("⚠️ Drawdown plot error:", e)

    # Telegram summary
    summary_text = (
        f"✅ Backtest Completed\n"
        f"Total Trades: {total_trades}\n"
        f"Total PnL: {total_pnl:.2f}\n"
        f"Wins/Losses: {int(wins)}/{int(losses)}\n"
        f"Max Drawdown: {overall_max_dd:.2f}%\n"
        f"Monthly report: {MONTHLY_CSV}"
    )
    send_message(summary_text)
    print("📁 All outputs in:", OUT_FOLDER)
    print("Done.")

if __name__ == "__main__":
    # optionally read initial balance from config.env or pass here
    INITIAL_BALANCE = 0.0
    generate_report(TRADES_CSV, INITIAL_BALANCE)
