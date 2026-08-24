"""
daily_pnl.py — "if I had put GBP 100 into each of the dashboard's top 10 picks,
how much would I have made that day?"

It now tracks THREE things side by side, so the question can actually be answered
rather than argued about:

  V1  the incumbent composite score   (tech .25 / fund .30 / mom .25 / sector .20)
  V2  a fundamentals-tilted challenger (tech .15 / fund .70 / mom .075 / sector .075)
  BM  the equal-weight universe, i.e. what you would have earned owning everything

V2 exists because a factor study over the first 40 days of history found the
incumbent's ranking had NO cross-sectional information at a one-day horizon
(long-minus-short t = -0.18), while the fundamental component on its own was the
only reliably positive one. 45% of V1's weight sat in the momentum and sector
components, which were anti-predictive over that window and cancelled it out.

That study was in-sample. V2 is therefore a CHALLENGER, not a replacement: both are
computed every day from here on, and the forward record is what decides between them.
Do not present V2's historical numbers as evidence that it works.

METHOD (deliberately free of look-ahead)
  signal date D    the ranking published at the END of day D, recovered from git
                   history (every price-update commit is a full data.json snapshot,
                   so the repo is its own archive)
  trade date D+1   buy the top 10 at D+1's OPEN, sell at D+1's CLOSE
  stake            GBP 100 per position => GBP 1,000 deployed per strategy per day
  FX               GBP converted at that morning's GBP/USD open, proceeds converted
                   back at the close
  costs            COST_BPS charged round-trip on every position, every day. The
                   strategy rebalances daily, so turnover is ~100% a day and costs
                   are not a rounding error — they are the main event.
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

# Round-trip trading cost in basis points. 20bps is already generous for a UK
# retail investor buying US shares: FX spread alone is typically 50-100bps EACH
# WAY at mainstream brokers, before commission.
COST_BPS = 20.0

# V1 is read straight from compositeScore as published. V2 is rebuilt from the
# component scores, which are present in every historical snapshot, so it can be
# backfilled over the same history on identical terms.
V2_WEIGHTS = {"technicalScore": 0.15, "fundamentalScore": 0.70,
              "momentumScore": 0.075, "sectorScore": 0.075}


def git(*args):
    return subprocess.run(
        ["git", "-C", str(HERE), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout


def extract_snapshots():
    """The last ranking published on each date -> full cross-section."""
    log = git("log", "--reverse", "--format=%H %cI", "--", "data.json").strip().splitlines()
    if not log:
        raise SystemExit("No history for data.json — the checkout needs fetch-depth: 0.")

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
        if len(stocks) < TOP_N:
            continue
        snaps[date] = stocks
    return snaps


def baskets_from(stocks):
    """Top-N ticker lists for V1 and V2, plus the full universe."""
    v1 = [s["ticker"] for s in
          sorted(stocks, key=lambda s: s.get("compositeScore") or 0, reverse=True)[:TOP_N]]

    def v2_score(s):
        return sum((s.get(k) or 0) * w for k, w in V2_WEIGHTS.items())

    v2 = [s["ticker"] for s in sorted(stocks, key=v2_score, reverse=True)[:TOP_N]]
    universe = [s["ticker"] for s in stocks]
    return v1, v2, universe


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


def leg_pnl(tickers, px, trade_day, fx_open, fx_close):
    """GBP P&L, gross and net of costs, for an equal-weight GBP100-per-name basket."""
    pnls, moves, filled = [], [], []
    for t in tickers:
        df = px.get(t)
        if df is None or trade_day not in df.index:
            continue
        o, c = float(df.loc[trade_day, "Open"]), float(df.loc[trade_day, "Close"])
        if not (o > 0 and c > 0):
            continue
        shares = (STAKE_GBP * fx_open) / o
        gross = (shares * c) / fx_close - STAKE_GBP
        pnls.append(gross)
        moves.append((c / o - 1) * 100)
        filled.append((t, gross, o, c))
    if not pnls:
        return None
    gross_total = sum(pnls)
    cost = len(pnls) * STAKE_GBP * COST_BPS / 10000.0
    return {
        "gross": gross_total,
        "net": gross_total - cost,
        "n": len(pnls),
        "wins": sum(1 for p in pnls if p > 0),
        "meanMovePct": sum(moves) / len(moves),
        "filled": filled,
    }


def main():
    snaps = extract_snapshots()
    if not snaps:
        raise SystemExit("No snapshots could be extracted.")

    signal_dates = sorted(snaps)
    universe = sorted({s["ticker"] for v in snaps.values() for s in v})
    start = (pd.Timestamp(signal_dates[0]) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(signal_dates[-1]) + pd.Timedelta(days=5)).strftime("%Y-%m-%d")

    print(f"{len(signal_dates)} signal days, {len(universe)} tickers, {start} -> {end}")
    px = fetch(universe, start, end)
    print(f"  {len(px)} price series")

    if "GBPUSD=X" not in px:
        print("GBP/USD unavailable — aborting without touching the CSVs.")
        return 1
    fx = px["GBPUSD=X"]

    cal = next((px[t] for t in ("CAT", "MU", "DAL", "PLTR", "AAPL") if t in px), None)
    if cal is None:
        cal = px[universe[0]]
    trading_days = list(cal.index)

    # The price workflow runs Mon-Fri and so publishes a ranking on market holidays
    # too, which makes several signal dates resolve to the same next open session
    # (02 and 03 Jul 2026 both -> Mon 06 Jul). Keep only the freshest ranking per
    # trade day, otherwise that day double-deploys capital and double-counts P&L.
    signal_for_trade_day = {}
    for sig in signal_dates:
        sig_ts = pd.Timestamp(sig)
        future = [d for d in trading_days if d > sig_ts]
        if future:
            signal_for_trade_day[future[0]] = sig

    daily_rows, position_rows = [], []

    for trade_day in sorted(signal_for_trade_day):
        sig = signal_for_trade_day[trade_day]
        if trade_day not in fx.index:
            continue
        fx_open, fx_close = float(fx.loc[trade_day, "Open"]), float(fx.loc[trade_day, "Close"])
        if not (fx_open > 0 and fx_close > 0):
            continue

        v1_t, v2_t, uni_t = baskets_from(snaps[sig])
        v1 = leg_pnl(v1_t, px, trade_day, fx_open, fx_close)
        v2 = leg_pnl(v2_t, px, trade_day, fx_open, fx_close)
        bm = leg_pnl(uni_t, px, trade_day, fx_open, fx_close)
        if not v1 or not v2 or not bm:
            continue

        best = max(v1["filled"], key=lambda x: x[1])
        worst = min(v1["filled"], key=lambda x: x[1])

        daily_rows.append({
            "TradeDate": trade_day.date(), "SignalDate": sig,
            "Positions": v1["n"], "DeployedGBP": round(v1["n"] * STAKE_GBP, 2),
            "PnLGBP": round(v1["gross"], 2),
            "ReturnPct": round(v1["gross"] / (v1["n"] * STAKE_GBP) * 100, 3),
            "NetPnLGBP": round(v1["net"], 2),
            "Winners": v1["wins"], "Losers": v1["n"] - v1["wins"],
            "HitRatePct": round(v1["wins"] / v1["n"] * 100, 1),
            "BestTicker": best[0], "BestPnLGBP": round(best[1], 2),
            "WorstTicker": worst[0], "WorstPnLGBP": round(worst[1], 2),
            "ChallengerPnLGBP": round(v2["gross"], 2),
            "ChallengerReturnPct": round(v2["gross"] / (v2["n"] * STAKE_GBP) * 100, 3),
            "ChallengerNetPnLGBP": round(v2["net"], 2),
            "ChallengerHitRatePct": round(v2["wins"] / v2["n"] * 100, 1),
            "UniverseReturnPct": round(bm["meanMovePct"], 3),
            "FXOpen": round(fx_open, 5), "FXClose": round(fx_close, 5),
        })

        for strat, leg in (("V1", v1), ("V2", v2)):
            for t, pnl, o, c in leg["filled"]:
                position_rows.append({
                    "TradeDate": trade_day.date(), "SignalDate": sig, "Strategy": strat,
                    "Ticker": t, "EntryUSD": round(o, 4), "ExitUSD": round(c, 4),
                    "MovePct": round((c / o - 1) * 100, 3),
                    "StakeGBP": STAKE_GBP, "PnLGBP": round(pnl, 4),
                })

    if not daily_rows:
        print("Nothing tradeable yet — CSVs left untouched.")
        return 1

    daily = pd.DataFrame(daily_rows).sort_values("TradeDate").reset_index(drop=True)
    for col, cum in (("PnLGBP", "CumulativePnLGBP"),
                     ("NetPnLGBP", "CumulativeNetPnLGBP"),
                     ("ChallengerPnLGBP", "ChallengerCumulativePnLGBP"),
                     ("ChallengerNetPnLGBP", "ChallengerCumulativeNetGBP")):
        daily[cum] = daily[col].cumsum().round(2)

    positions = pd.DataFrame(position_rows).sort_values(
        ["TradeDate", "Strategy", "PnLGBP"], ascending=[True, True, False])

    daily.to_csv(HERE / "daily_pnl.csv", index=False)
    positions.to_csv(HERE / "positions_pnl.csv", index=False)

    def summarise(name, gross_col, ret_col):
        g, r = daily[gross_col], daily[ret_col]
        n = len(daily)
        sd = r.std()
        t = r.mean() / (sd / n ** 0.5) if sd else 0.0
        print(f"  {name:<12} gross GBP {g.sum():>8.2f} | {r.mean():+.3f}%/day | "
              f"t {t:+.2f} | up days {int((g > 0).sum())}/{n}")

    print(f"\n{len(daily)} trading days: {daily['TradeDate'].min()} -> {daily['TradeDate'].max()}")
    summarise("V1 incumbent", "PnLGBP", "ReturnPct")
    summarise("V2 challenger", "ChallengerPnLGBP", "ChallengerReturnPct")
    print(f"  {'Benchmark':<12} {'':>14} | {daily['UniverseReturnPct'].mean():+.3f}%/day "
          f"(equal-weight universe, open->close)")
    print(f"\n  after {COST_BPS:.0f}bps round-trip costs: "
          f"V1 GBP {daily['NetPnLGBP'].sum():.2f} | V2 GBP {daily['ChallengerNetPnLGBP'].sum():.2f}")
    print("\n  V2's history is IN-SAMPLE — its weights were chosen after seeing this data.")
    print("  Only the record from today forward is a fair test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
