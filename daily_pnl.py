"""
daily_pnl.py — answers one question every trading day:

    "If I had put GBP 100 into each of the dashboard's top 10 picks,
     how much would I have made that day?"

METHOD (deliberately free of look-ahead bias)
---------------------------------------------
  signal date D    the top 10 of the LAST ranking this repo published on day D,
                   recovered from git history (each price-update commit is a
                   full snapshot of data.json, so the repo is its own archive)
  trade date D+1   buy all 10 at D+1's OPEN, sell at D+1's CLOSE
  stake            GBP 100 per position => GBP 1,000 deployed per day
  FX               GBP 100 is converted to USD at that morning's GBP/USD open
                   and the proceeds are converted back at the close, so the
                   P&L is what would actually land in a GBP account

You could only ever act on a ranking published *before* the day you trade, which
is why the signal is taken from D and the trade from D+1. Ranking and trading on
the same bar would leak future information and flatter the result.

The script is idempotent: it recomputes every day it can and rewrites both CSVs,
so a missed scheduled run is simply healed by the next one.
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
HERE = Path(__file__).resolve().parent


def git(*args):
    return subprocess.run(
        ["git", "-C", str(HERE), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout


def extract_baskets():
    """The last ranking published on each date -> its top N tickers."""
    log = git("log", "--reverse", "--format=%H %cI", "--", "data.json").strip().splitlines()
    if not log:
        raise SystemExit("No history for data.json — the checkout needs fetch-depth: 0.")

    last_of_day = {}
    for line in log:
        sha, iso = line.split()
        last_of_day[iso[:10]] = sha          # oldest-first, so the last write wins

    baskets = {}
    for date, sha in sorted(last_of_day.items()):
        try:
            data = json.loads(git("show", f"{sha}:data.json"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        stocks = data.get("stocks", [])
        if len(stocks) < TOP_N:
            continue
        ranked = sorted(stocks, key=lambda s: s.get("compositeScore") or 0, reverse=True)
        baskets[date] = [s["ticker"] for s in ranked[:TOP_N]]
    return baskets


def fetch(tickers, start, end):
    syms = sorted(set(tickers)) + ["GBPUSD=X"]
    raw = yf.download(
        syms, start=start, end=end, auto_adjust=False,
        group_by="ticker", progress=False, threads=True,
    )
    out = {}
    for s in syms:
        try:
            df = raw[s][["Open", "Close"]].dropna()
        except KeyError:
            continue
        if not df.empty:
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            out[s] = df
    return out


def main():
    baskets = extract_baskets()
    if not baskets:
        raise SystemExit("No baskets could be extracted.")

    signal_dates = sorted(baskets)
    universe = sorted({t for v in baskets.values() for t in v})
    start = (pd.Timestamp(signal_dates[0]) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(signal_dates[-1]) + pd.Timedelta(days=5)).strftime("%Y-%m-%d")

    print(f"{len(signal_dates)} signal days, {len(universe)} distinct tickers, {start} -> {end}")
    px = fetch(universe, start, end)

    if "GBPUSD=X" not in px:
        # Never leave a good CSV overwritten by a bad download.
        print("GBP/USD unavailable — aborting without touching the CSVs.")
        return 1
    fx = px["GBPUSD=X"]

    # FX trades on days the US market is shut, so take the NYSE calendar from a
    # liquid equity instead.
    cal_src = next((px[t] for t in ("CAT", "MU", "DAL", "PLTR") if t in px), None)
    if cal_src is None:
        cal_src = px[universe[0]]
    trading_days = list(cal_src.index)

    # Map each signal to the next day the market is actually open. The price
    # workflow runs Mon-Fri and so also publishes a ranking on market holidays,
    # which means several signal dates can point at the same trade day (e.g. the
    # 02 and 03 Jul 2026 signals both point at Mon 06 Jul, because 03 Jul was the
    # observed Independence Day holiday). Keep only the freshest ranking for each
    # trade day — otherwise that day double-deploys capital and double-counts P&L.
    signal_for_trade_day = {}
    for sig in signal_dates:
        sig_ts = pd.Timestamp(sig)
        future = [d for d in trading_days if d > sig_ts]
        if not future:
            continue                                  # signal not yet tradeable
        signal_for_trade_day[future[0]] = sig         # later signal overwrites earlier

    position_rows, daily_rows = [], []

    for trade_day in sorted(signal_for_trade_day):
        sig = signal_for_trade_day[trade_day]
        if trade_day not in fx.index:
            continue

        fx_open, fx_close = float(fx.loc[trade_day, "Open"]), float(fx.loc[trade_day, "Close"])
        if not (fx_open > 0 and fx_close > 0):
            continue

        day_pnl = day_deployed = 0.0
        filled = []

        for ticker in baskets[sig]:
            df = px.get(ticker)
            if df is None or trade_day not in df.index:
                position_rows.append({
                    "TradeDate": trade_day.date(), "SignalDate": sig, "Ticker": ticker,
                    "EntryUSD": None, "ExitUSD": None, "MovePct": None,
                    "StakeGBP": 0.0, "PnLGBP": 0.0, "Filled": "NO DATA",
                })
                continue

            o, c = float(df.loc[trade_day, "Open"]), float(df.loc[trade_day, "Close"])
            if not (o > 0 and c > 0):
                continue

            shares = (STAKE_GBP * fx_open) / o          # GBP -> USD -> fractional shares
            pnl = (shares * c) / fx_close - STAKE_GBP   # sell, USD -> GBP

            day_pnl += pnl
            day_deployed += STAKE_GBP
            filled.append((ticker, pnl))

            position_rows.append({
                "TradeDate": trade_day.date(), "SignalDate": sig, "Ticker": ticker,
                "EntryUSD": round(o, 4), "ExitUSD": round(c, 4),
                "MovePct": round((c / o - 1) * 100, 3),
                "StakeGBP": STAKE_GBP, "PnLGBP": round(pnl, 4), "Filled": "YES",
            })

        if not filled:
            continue

        best = max(filled, key=lambda x: x[1])
        worst = min(filled, key=lambda x: x[1])
        wins = sum(1 for _, p in filled if p > 0)

        daily_rows.append({
            "TradeDate": trade_day.date(), "SignalDate": sig,
            "Positions": len(filled), "DeployedGBP": round(day_deployed, 2),
            "PnLGBP": round(day_pnl, 2),
            "ReturnPct": round(day_pnl / day_deployed * 100, 3),
            "Winners": wins, "Losers": len(filled) - wins,
            "HitRatePct": round(wins / len(filled) * 100, 1),
            "BestTicker": best[0], "BestPnLGBP": round(best[1], 2),
            "WorstTicker": worst[0], "WorstPnLGBP": round(worst[1], 2),
            "FXOpen": round(fx_open, 5), "FXClose": round(fx_close, 5),
        })

    if not daily_rows:
        print("Nothing tradeable yet — CSVs left untouched.")
        return 1

    daily = pd.DataFrame(daily_rows).sort_values("TradeDate").reset_index(drop=True)
    daily["CumulativePnLGBP"] = daily["PnLGBP"].cumsum().round(2)
    positions = pd.DataFrame(position_rows).sort_values(
        ["TradeDate", "PnLGBP"], ascending=[True, False]
    )

    daily.to_csv(HERE / "daily_pnl.csv", index=False)
    positions.to_csv(HERE / "positions_pnl.csv", index=False)

    n, total = len(daily), daily["PnLGBP"].sum()
    up = int((daily["PnLGBP"] > 0).sum())
    mean, sd = daily["ReturnPct"].mean(), daily["ReturnPct"].std()
    t_stat = mean / (sd / n ** 0.5) if sd else 0.0

    print(f"\n{n} trading days: {daily['TradeDate'].min()} -> {daily['TradeDate'].max()}")
    print(f"total P&L GBP {total:,.2f} | avg GBP {daily['PnLGBP'].mean():,.2f}/day ({mean:+.3f}%)")
    print(f"profitable days {up}/{n} ({up / n * 100:.1f}%) | position hit rate {daily['HitRatePct'].mean():.1f}%")
    print(f"daily vol {sd:.3f}% | t-stat {t_stat:+.2f} "
          f"({'significant' if abs(t_stat) > 2 else 'NOT distinguishable from zero'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
