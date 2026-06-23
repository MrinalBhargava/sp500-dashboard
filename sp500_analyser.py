"""
sp500_analyser.py — runs every 15 min during market hours via GitHub Actions.
Downloads prices for all S&P 500 stocks, computes multi-strategy composite scores,
generates a template-based thesis for the top pick, and writes data.js.
"""

import json
import math
import os
import warnings
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")


def _sanitize(obj):
    """Recursively replace NaN/inf with None so json.dumps never raises."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


SECTOR_ETFS = {
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}


# ── Tickers ───────────────────────────────────────────────────────────────────

def get_sp500():
    try:
        import io
        import requests as _req
        html = _req.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0 SP500-Dashboard/1.0"},
            timeout=15,
        ).text
        table = pd.read_html(io.StringIO(html))[0]
        tickers = table["Symbol"].str.replace(".", "-", regex=False).tolist()
        names = dict(zip(
            table["Symbol"].str.replace(".", "-", regex=False),
            table["Security"]
        ))
        sectors = dict(zip(
            table["Symbol"].str.replace(".", "-", regex=False),
            table["GICS Sector"]
        ))
        return tickers, names, sectors
    except Exception as e:
        print(f"Wikipedia fetch failed: {e}, using cached list")
        if os.path.exists("sp500_meta.json"):
            with open("sp500_meta.json", encoding="utf-8") as f:
                meta = json.load(f)
            return meta["tickers"], meta["names"], meta["sectors"]
        raise


# ── Technical indicators ──────────────────────────────────────────────────────

def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).iloc[-1]


def macd_signal(close, fast=12, slow=26, sig=9):
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    signal = macd.ewm(span=sig, adjust=False).mean()
    return float(macd.iloc[-1]), float(signal.iloc[-1])


def bollinger_pct(close, period=20):
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    price = close.iloc[-1]
    denom = float(upper.iloc[-1] - lower.iloc[-1])
    if denom == 0 or math.isnan(denom):
        return 0.5
    return float((price - lower.iloc[-1]) / denom)


def compute_technicals(hist):
    close = hist["Close"].squeeze()
    volume = hist["Volume"].squeeze()

    rsi_val = rsi(close)
    macd_val, sig_val = macd_signal(close)
    bb_pct = bollinger_pct(close)
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    price = float(close.iloc[-1])
    vol_ratio = float(volume.iloc[-1] / volume.rolling(20).mean().iloc[-1]) if volume.rolling(20).mean().iloc[-1] > 0 else 1.0

    # Component scores 0-100
    if rsi_val < 30:
        rsi_score = 88
    elif rsi_val < 45:
        rsi_score = 75
    elif rsi_val < 55:
        rsi_score = 58
    elif rsi_val < 65:
        rsi_score = 42
    elif rsi_val < 75:
        rsi_score = 28
    else:
        rsi_score = 15

    macd_score = 72 if macd_val > sig_val else 28

    bb_score = max(0, min(100, (1 - bb_pct) * 100))

    if price > ma50 and price > ma200 and ma50 > ma200:
        ma_score = 90  # golden cross + price above both
    elif price > ma50 and price > ma200:
        ma_score = 78
    elif price > ma200:
        ma_score = 55
    elif price > ma50:
        ma_score = 45
    else:
        ma_score = 20

    vol_score = min(100, vol_ratio * 55) if vol_ratio > 1 else max(10, vol_ratio * 40)

    score = (rsi_score * 0.30 + macd_score * 0.25 + bb_score * 0.20 + ma_score * 0.15 + vol_score * 0.10)

    return {
        "score": round(score, 1),
        "rsi": round(rsi_val, 1),
        "macd": "bullish" if macd_val > sig_val else "bearish",
        "macd_value": round(macd_val, 3),
        "bb_pct": round(bb_pct, 3),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "price": round(price, 2),
        "vol_ratio": round(vol_ratio, 2),
    }


def compute_momentum(hist):
    close = hist["Close"].squeeze()
    price = float(close.iloc[-1])

    def pct(n):
        return round((price / float(close.iloc[-n]) - 1) * 100, 2) if len(close) > n else 0.0

    return {"m1": pct(21), "m3": pct(63), "m6": pct(126)}


# ── Percentile-rank scoring helpers ───────────────────────────────────────────

def pct_rank(series, ascending=True):
    """Return percentile rank 0–100 for each element; ascending=True means higher value → higher rank."""
    s = pd.Series(series, dtype=float)
    if ascending:
        return s.rank(pct=True) * 100
    else:
        return (1 - s.rank(pct=True)) * 100 + 1


# ── Thesis generation ─────────────────────────────────────────────────────────

def generate_thesis(ticker, name, tech, fund, mom, sector_outperf_3m, composite, rank, total):
    pe = fund.get("pe")
    sector_pe = fund.get("sector_pe")
    rsi_val = tech["rsi"]
    macd = tech["macd"]
    m3 = mom["m3"]
    eps_growth = fund.get("eps_growth", 0) or 0
    fcf_yield = fund.get("fcf_yield", 0) or 0

    # Sentence 1: valuation + technical
    if pe and sector_pe and pe > 0 and sector_pe > 0:
        diff_pct = (sector_pe - pe) / sector_pe * 100
        if diff_pct > 5:
            val_phrase = f"trading at {pe:.1f}x P/E, a {abs(diff_pct):.0f}% discount to its sector average of {sector_pe:.1f}x"
        elif diff_pct < -5:
            val_phrase = f"trading at {pe:.1f}x P/E, a {abs(diff_pct):.0f}% premium to its sector peers of {sector_pe:.1f}x"
        else:
            val_phrase = f"trading at {pe:.1f}x P/E, broadly in line with sector peers at {sector_pe:.1f}x"
    elif fcf_yield and fcf_yield > 0:
        val_phrase = f"generating a free cash flow yield of {fcf_yield*100:.1f}% with strong balance sheet fundamentals"
    else:
        val_phrase = "demonstrating strong relative fundamentals within its sector peer group"

    if rsi_val < 35:
        rsi_phrase = f"RSI at {rsi_val:.0f} flags significantly oversold conditions with mean-reversion potential"
    elif rsi_val < 50:
        rsi_phrase = f"RSI at {rsi_val:.0f} indicates recovering momentum from recent lows"
    elif rsi_val < 65:
        rsi_phrase = f"RSI at {rsi_val:.0f} reflects healthy bullish momentum without overextension"
    else:
        rsi_phrase = f"RSI at {rsi_val:.0f} reflects strong but extended momentum — watch for potential consolidation"

    s1 = f"{name} ({ticker}) is {val_phrase}; {rsi_phrase}."

    # Sentence 2: momentum + MACD
    sign = "+" if m3 >= 0 else ""
    if abs(m3) > 15:
        mom_str = "exceptional"
    elif abs(m3) > 8:
        mom_str = "strong"
    elif abs(m3) > 3:
        mom_str = "moderate"
    else:
        mom_str = "subdued"

    direction = "accumulation" if m3 >= 0 else "distribution"
    macd_phrase = (
        "a bullish MACD crossover confirming upward price pressure"
        if macd == "bullish"
        else "a bearish MACD signal suggesting near-term caution is warranted"
    )
    eps_phrase = f", with EPS growing {eps_growth*100:+.0f}% year-on-year" if abs(eps_growth) > 0.02 else ""

    s2 = f"Three-month price momentum of {sign}{m3:.1f}% reflects {mom_str} institutional {direction}{eps_phrase}, underpinned by {macd_phrase}."

    # Sentence 3: composite rank + sector outperformance
    percentile = max(1, round(rank / total * 100))
    if abs(sector_outperf_3m) > 1:
        outperf_phrase = f"outperforming its sector by {sector_outperf_3m:+.1f}% over the past quarter"
    else:
        outperf_phrase = "broadly tracking its sector over the past quarter"

    s3 = f"Composite score of {composite:.0f}/100 places {ticker} in the top {percentile}% of S&P 500 constituents across all four analytical strategies, {outperf_phrase}."

    return f"{s1} {s2} {s3}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Fetching S&P 500 constituents...")
    tickers, names, sectors = get_sp500()

    # Save meta for fallback
    with open("sp500_meta.json", "w", encoding="utf-8") as f:
        json.dump({"tickers": tickers, "names": names, "sectors": sectors}, f)

    # All tickers we need: S&P 500 + sector ETFs
    all_etfs = list(set(SECTOR_ETFS.values()))
    all_tickers = tickers + all_etfs

    print(f"Downloading price history for {len(tickers)} stocks + {len(all_etfs)} ETFs...")
    raw = yf.download(
        all_tickers,
        period="1y",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
    )

    # Load cached fundamentals
    fund_cache = {}
    if os.path.exists("fundamentals_cache.json"):
        with open("fundamentals_cache.json", encoding="utf-8") as f:
            fund_cache = json.load(f)
    else:
        print("WARNING: No fundamentals cache found. Run refresh_fundamentals.py first.")

    # Sector ETF momentum
    etf_mom = {}
    for sector, etf in SECTOR_ETFS.items():
        try:
            if etf not in raw.columns.get_level_values(0):
                etf_mom[sector] = {"m1": 0, "m3": 0, "m6": 0}
                continue
            hist = raw[etf]
            m = compute_momentum(hist)
            etf_mom[sector] = m
        except Exception:
            etf_mom[sector] = {"m1": 0, "m3": 0, "m6": 0}

    # Compute per-stock signals
    print("Computing signals...")
    results = []

    for ticker in tickers:
        try:
            if ticker not in raw.columns.get_level_values(0):
                continue
            hist = raw[ticker]
            if hist.empty or len(hist) < 60:
                continue

            tech = compute_technicals(hist)
            mom = compute_momentum(hist)
            fund = fund_cache.get(ticker, {})
            sector = sectors.get(ticker, "Unknown")

            # Sector outperformance
            etf_m3 = etf_mom.get(sector, {}).get("m3", 0)
            sector_outperf_3m = mom["m3"] - etf_m3

            results.append({
                "ticker": ticker,
                "name": names.get(ticker, ticker),
                "sector": sector,
                "tech": tech,
                "mom": mom,
                "fund": fund,
                "sector_outperf_3m": sector_outperf_3m,
                "price": tech["price"],
            })
        except Exception as e:
            print(f"  Skip {ticker}: {e}")

    if not results:
        print("No results computed — aborting.")
        return

    print(f"Computed signals for {len(results)} stocks.")

    # ── Percentile-rank fundamental scores ────────────────────────────────────
    # Build arrays for normalization
    n = len(results)

    def _fval(d, key):
        v = d.get(key)
        return float(v) if v is not None else np.nan

    pe_arr = np.array([_fval(r["fund"], "pe") for r in results])
    eps_arr = np.array([_fval(r["fund"], "eps_growth") for r in results])
    rev_arr = np.array([_fval(r["fund"], "rev_growth") for r in results])
    fcf_arr = np.array([_fval(r["fund"], "fcf_yield") for r in results])
    roe_arr = np.array([_fval(r["fund"], "roe") for r in results])
    de_arr = np.array([_fval(r["fund"], "debt_equity") for r in results])

    def pct_rank_arr(arr, ascending=True):
        series = pd.Series(arr)
        filled = series.fillna(series.median())
        ranks = filled.rank(pct=True)
        return (ranks * 100).values if ascending else ((1 - ranks) * 100).values

    pe_rank = pct_rank_arr(pe_arr, ascending=False)    # lower P/E → higher score
    eps_rank = pct_rank_arr(eps_arr, ascending=True)
    rev_rank = pct_rank_arr(rev_arr, ascending=True)
    fcf_rank = pct_rank_arr(fcf_arr, ascending=True)
    roe_rank = pct_rank_arr(roe_arr, ascending=True)
    de_rank = pct_rank_arr(de_arr, ascending=False)    # lower D/E → higher score

    # ── Percentile-rank momentum scores ───────────────────────────────────────
    m1_arr = np.array([r["mom"]["m1"] for r in results])
    m3_arr = np.array([r["mom"]["m3"] for r in results])
    m6_arr = np.array([r["mom"]["m6"] for r in results])

    m1_rank = pct_rank_arr(m1_arr)
    m3_rank = pct_rank_arr(m3_arr)
    m6_rank = pct_rank_arr(m6_arr)

    # ── Sector relative strength scores (within-sector percentile) ────────────
    sector_outperf_arr = np.array([r["sector_outperf_3m"] for r in results])
    sector_labels = [r["sector"] for r in results]

    sector_rs_scores = np.zeros(n)
    for sec in set(sector_labels):
        idx = [i for i, s in enumerate(sector_labels) if s == sec]
        if len(idx) < 2:
            sector_rs_scores[idx[0]] = 50
            continue
        vals = pd.Series(sector_outperf_arr[idx])
        ranks = vals.rank(pct=True) * 100
        for pos, i in enumerate(idx):
            sector_rs_scores[i] = float(ranks.iloc[pos])

    # ── Compute sector P/E averages ───────────────────────────────────────────
    sector_pe_map = {}
    for sec in set(sector_labels):
        idx = [i for i, s in enumerate(sector_labels) if s == sec]
        pes = [pe_arr[i] for i in idx if not np.isnan(pe_arr[i]) and pe_arr[i] > 0]
        sector_pe_map[sec] = round(float(np.median(pes)), 1) if pes else None

    # Inject sector P/E into fund data
    for r in results:
        r["fund"]["sector_pe"] = sector_pe_map.get(r["sector"])

    # ── Composite scores ──────────────────────────────────────────────────────
    output = []
    for i, r in enumerate(results):
        fund_score = (
            pe_rank[i] * 0.22 +
            eps_rank[i] * 0.20 +
            rev_rank[i] * 0.18 +
            fcf_rank[i] * 0.18 +
            roe_rank[i] * 0.12 +
            de_rank[i] * 0.10
        )
        mom_score = m1_rank[i] * 0.20 + m3_rank[i] * 0.40 + m6_rank[i] * 0.40
        sector_score = sector_rs_scores[i]
        tech_score = r["tech"]["score"]

        composite = (
            tech_score * 0.25 +
            fund_score * 0.30 +
            mom_score * 0.25 +
            sector_score * 0.20
        )

        output.append({
            **r,
            "composite": round(composite, 1),
            "fund_score": round(fund_score, 1),
            "mom_score": round(mom_score, 1),
            "sector_score": round(sector_score, 1),
        })

    # Sort by composite descending
    output.sort(key=lambda x: x["composite"], reverse=True)

    # Assign ranks
    for rank_i, item in enumerate(output):
        item["rank"] = rank_i + 1

    # ── Load previous data for rank deltas ────────────────────────────────────
    prev_ranks = {}
    if os.path.exists("data_prev.js"):
        import re
        with open("data_prev.js", encoding="utf-8", errors="replace") as f:
            content = f.read()
        matches = re.findall(r'"ticker"\s*:\s*"([A-Z\-]+)"[^}]*"rank"\s*:\s*(\d+)', content)
        for t, r_ in matches:
            prev_ranks[t] = int(r_)

    total = len(output)
    top = output[0]

    # ── Generate thesis for top pick ─────────────────────────────────────────
    top_thesis = generate_thesis(
        top["ticker"], top["name"], top["tech"], top["fund"],
        top["mom"], top["sector_outperf_3m"],
        top["composite"], top["rank"], total
    )

    # ── Market status ─────────────────────────────────────────────────────────
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    et_hour = now_et.hour
    et_minute = now_et.minute
    weekday = now_et.weekday()  # 0=Mon, 4=Fri
    if weekday >= 5:
        market_status = "closed"
    elif et_hour < 9 or (et_hour == 9 and et_minute < 30):
        market_status = "pre-market"
    elif et_hour < 16:
        market_status = "open"
    else:
        market_status = "after-hours"

    # ── Sector aggregates ─────────────────────────────────────────────────────
    sector_data = {}
    for sec in set(sector_labels):
        sec_stocks = [x for x in output if x["sector"] == sec]
        if not sec_stocks:
            continue
        avg_score = round(np.mean([x["composite"] for x in sec_stocks]), 1)
        top_stock = sec_stocks[0]["ticker"]
        etf_m = etf_mom.get(sec, {})
        sector_data[sec] = {
            "avgScore": avg_score,
            "topStock": top_stock,
            "stockCount": len(sec_stocks),
            "etf": SECTOR_ETFS.get(sec, ""),
            "etfReturn1m": round(etf_m.get("m1", 0), 2),
            "etfReturn3m": round(etf_m.get("m3", 0), 2),
        }

    # ── Serialise stock list ──────────────────────────────────────────────────
    def stock_obj(x):
        prev_rank = prev_ranks.get(x["ticker"], x["rank"])
        return {
            "ticker": x["ticker"],
            "name": x["name"],
            "sector": x["sector"],
            "price": x["price"],
            "changePct": round(
                (x["price"] / x["tech"].get("ma50", x["price"]) - 1) * 0 +
                (x["mom"]["m1"] / 21 if x["mom"]["m1"] else 0), 2
            ),  # approximate daily move from 1M momentum
            "compositeScore": x["composite"],
            "technicalScore": x["tech"]["score"],
            "fundamentalScore": x["fund_score"],
            "momentumScore": x["mom_score"],
            "sectorScore": x["sector_score"],
            "rank": x["rank"],
            "prevRank": prev_rank,
            "signals": {
                "rsi": x["tech"]["rsi"],
                "macd": x["tech"]["macd"],
                "bbPct": x["tech"]["bb_pct"],
                "ma50": x["tech"]["ma50"],
                "ma200": x["tech"]["ma200"],
                "volRatio": x["tech"]["vol_ratio"],
                "pe": x["fund"].get("pe"),
                "sectorPe": x["fund"].get("sector_pe"),
                "epsGrowth": x["fund"].get("eps_growth"),
                "revGrowth": x["fund"].get("rev_growth"),
                "fcfYield": x["fund"].get("fcf_yield"),
                "roe": x["fund"].get("roe"),
                "debtEquity": x["fund"].get("debt_equity"),
                "momentum1m": x["mom"]["m1"],
                "momentum3m": x["mom"]["m3"],
                "momentum6m": x["mom"]["m6"],
                "sectorOutperf3m": round(x["sector_outperf_3m"], 2),
            },
        }

    top_obj = stock_obj(top)
    top_obj["thesis"] = top_thesis

    stocks_list = [stock_obj(x) for x in output]

    # ── Write data.js ─────────────────────────────────────────────────────────
    # Save current as previous before overwriting
    if os.path.exists("data.js"):
        import shutil
        shutil.copy("data.js", "data_prev.js")

    payload = {
        "lastUpdated": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "marketStatus": market_status,
        "totalStocks": total,
        "topPick": top_obj,
        "stocks": stocks_list,
        "sectors": sector_data,
    }

    with open("data.js", "w", encoding="utf-8") as f:
        f.write("// Auto-generated by sp500_analyser.py - do not edit manually\n")
        f.write(f"const DATA = {json.dumps(_sanitize(payload), indent=2)};\n")

    print(f"data.js written — top pick: {top['ticker']} ({top['composite']:.1f}/100) | {total} stocks | status: {market_status}")


if __name__ == "__main__":
    main()
