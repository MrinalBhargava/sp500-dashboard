"""
monthly_pnl.py — the structural fix for the cost problem, not a weight tweak.

daily_pnl.py established two facts:
  1. The fundamental signal's Information Coefficient RISES with horizon
     (+0.05 at 1 day -> +0.22 at 20 days) — it is a slow signal.
  2. Daily rebalancing charges a round-trip cost on ~100% of the book EVERY DAY.
     At a realistic 100-150bps UK-retail round trip (FX spread each way plus
     commission), that is GBP 4-6/day on GBP1,000 deployed — enough to erase
     the entire daily edge on its own, before the signal is even judged.

A slow signal cannot pay for daily turnover. So this script does not trade
daily. It rebalances once a MONTH: buy the top 10 by challengerScore (V2) at
the open of the first trading day of each calendar month, using the most
recent ranking published before that day (no look-ahead), and hold — no
selling, no re-scoring — until the next month's rebalance day, or to date if
the holding period is still open.

Cost is charged ONCE per position per holding period (one buy, one sell),
not once per day. That is the entire structural difference from the daily
strategy, and it is what determines whether this can ever be economic for a
retail investor.

HONESTY NOTE, READ BEFORE QUOTING ANY NUMBER FROM THIS FILE:
This repo's history currently spans under two months. That gives at most ONE
completed monthly holding period plus one still-open one. One data point is
not a backtest — it is an anecdote. This script exists to start accumulating
real periods from here on; it is not evidence yet, and will not be for months.
"""

import json
import subprocess
import sys
import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

TOP_N = 10
STAKE_GBP = 100.0
COST_BPS = 20.0          # same convention as daily_pnl.py: round-trip bps
HERE = Path(__file__).resolve().parent

V2_WEIGHTS = {"technicalScore": 0.15, "fundamentalScore": 0.70,
              "momentumScore": 0.075, "sectorScore": 0.075}


def git(*args):
    return subprocess.run(
        ["git", "-C", str(HERE), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout


def extract_snapshots():
    log = git("log", "--reverse", "--format=%H %cI", "--", "data.json").strip().splitlines()
    if not log:
        raise SystemExit("No history for data.json — checkout needs fetch-depth: 0.")
    last_of_day = {}
    for line in log:
        sha, iso = line.split()
        last_of_day[iso[:10]] = sha
    snaps = {}
    for date, sha in sorted(last_of_day.items()):
        try:
            data = json.loads(git("show", f"{sha}:data.json"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        stocks = data.get("stocks", [])
        if len(stocks) >= TOP_N:
            snaps[date] = stocks
    return snaps


def v2_top10(stocks):
    def score(s):
        return sum((s.get(k) or 0) * w for k, w in V2_WEIGHTS.items())
    return [s["ticker"] for s in sorted(stocks, key=score, reverse=True)[:TOP_N]]


def fetch(tickers, start, end):
    syms = sorted(set(tickers)) + ["GBPUSD=X"]
    out = {}
    chunk = 150
    for i in range(0, len(syms), chunk):
        raw = yf.download(syms[i:i + chunk], start=start, end=end, auto_adjust=False,
                          group_by="ticker", progress=False, threads=True)
        for s in syms[i:i + chunk]:
            try:
                df = raw[s][["Open", "Close"]].dropna()
            except KeyError:
                continue
            if not df.empty:
                df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                out[s] = df
    return out


def month_start_trading_days(trading_days):
    seen, starts = set(), []
    for d in sorted(trading_days):
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            starts.append(d)
    return starts


def main():
    snaps = extract_snapshots()
    if not snaps:
        raise SystemExit("No snapshots.")
    signal_dates = sorted(snaps)
    universe = sorted({s["ticker"] for v in snaps.values() for s in v})
    start = (pd.Timestamp(signal_dates[0]) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(signal_dates[-1]) + pd.Timedelta(days=5)).strftime("%Y-%m-%d")

    print(f"{len(signal_dates)} signal days, {len(universe)} tickers, {start} -> {end}")
    px = fetch(universe, start, end)
    if "GBPUSD=X" not in px:
        print("GBP/USD unavailable — aborting without touching the CSVs.")
        return 1
    fx = px["GBPUSD=X"]

    cal = next((px[t] for t in ("CAT", "MU", "DAL", "PLTR", "AAPL") if t in px), None)
    if cal is None:
        cal = px[universe[0]]
    trading_days = list(cal.index)

    # Rebalance on the first trading day of every calendar month that (a) has
    # price data and (b) has a ranking published strictly before it.
    candidates = month_start_trading_days(trading_days)
    rebalances = [d for d in candidates if any(pd.Timestamp(s) < d for s in signal_dates)]
    if not rebalances:
        print("Not enough history yet for even one monthly rebalance point.")
        return 1

    def snapshot_before(day):
        prior = [s for s in signal_dates if pd.Timestamp(s) < day]
        return snaps[max(prior)] if prior else None

    period_rows, position_rows = [], []

    for i, entry_day in enumerate(rebalances):
        if entry_day not in fx.index:
            continue
        snap = snapshot_before(entry_day)
        if snap is None:
            continue
        basket = v2_top10(snap)

        is_last = (i == len(rebalances) - 1)
        exit_day = rebalances[i + 1] if not is_last else trading_days[-1]
        exit_field = "Open" if not is_last else "Close"   # sell at next rebalance's open, or mark at latest close
        status = "CLOSED" if not is_last else "OPEN (mark-to-market)"

        if exit_day not in fx.index:
            continue
        fx_entry = float(fx.loc[entry_day, "Open"])
        fx_exit = float(fx.loc[exit_day, exit_field])
        if not (fx_entry > 0 and fx_exit > 0):
            continue

        gross_total, filled = 0.0, []
        for t in basket:
            df = px.get(t)
            if df is None or entry_day not in df.index or exit_day not in df.index:
                continue
            o = float(df.loc[entry_day, "Open"])
            c = float(df.loc[exit_day, exit_field])
            if not (o > 0 and c > 0):
                continue
            shares = (STAKE_GBP * fx_entry) / o
            gross = (shares * c) / fx_exit - STAKE_GBP
            gross_total += gross
            filled.append((t, gross, o, c))
            position_rows.append({
                "Period": i + 1, "EntryDate": entry_day.date(), "ExitDate": exit_day.date(),
                "Status": status, "Ticker": t, "EntryUSD": round(o, 4), "ExitUSD": round(c, 4),
                "MovePct": round((c / o - 1) * 100, 3), "StakeGBP": STAKE_GBP,
                "PnLGBP": round(gross, 4),
            })

        if not filled:
            continue
        n = len(filled)
        cost = n * STAKE_GBP * COST_BPS / 10000.0 if status == "CLOSED" else 0.0
        period_rows.append({
            "Period": i + 1, "EntryDate": entry_day.date(), "ExitDate": exit_day.date(),
            "HoldingDays": int((exit_day - entry_day).days), "Status": status,
            "Positions": n, "DeployedGBP": round(n * STAKE_GBP, 2),
            "GrossPnLGBP": round(gross_total, 2),
            "NetPnLGBP": round(gross_total - cost, 2),
            "ReturnPct": round(gross_total / (n * STAKE_GBP) * 100, 3),
            "Basket": " ".join(basket),
        })

    if not period_rows:
        print("No complete holding periods yet.")
        return 1

    periods = pd.DataFrame(period_rows)
    positions = pd.DataFrame(position_rows)
    periods.to_csv(HERE / "monthly_pnl.csv", index=False)
    positions.to_csv(HERE / "monthly_positions.csv", index=False)

    closed = periods[periods.Status == "CLOSED"]
    open_ = periods[periods.Status != "CLOSED"]

    print(f"\n{len(periods)} monthly period(s) extracted "
          f"({len(closed)} closed, {len(open_)} open/mark-to-market)")
    print(periods[["Period", "EntryDate", "ExitDate", "Status", "Positions",
                   "GrossPnLGBP", "NetPnLGBP", "ReturnPct"]].to_string(index=False))

    if len(closed):
        print(f"\nRealised so far (closed periods only): "
              f"gross GBP {closed.GrossPnLGBP.sum():.2f} | net GBP {closed.NetPnLGBP.sum():.2f}")
    if len(open_):
        print(f"Unrealised (still open, marked at latest close): "
              f"GBP {open_.GrossPnLGBP.sum():.2f}")

    print(f"\nN = {len(periods)}. This is NOT enough to compute a t-statistic or draw any")
    print("conclusion about whether monthly rebalancing works. It is the actual realised")
    print("result of the only period(s) that have happened so far. Treat as a running")
    print("ledger, not a verdict, until at least 10-12 periods have accumulated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
