"""
refresh_fundamentals.py — runs daily at 8am ET via GitHub Actions.
Fetches fundamental data for all S&P 500 stocks via yfinance and writes
fundamentals_cache.json. Keeps the 15-min script fast by caching quarterly data.
"""

import json
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")


def fetch_fundamentals(ticker):
    try:
        time.sleep(0.1)  # per-thread throttle to avoid rate limiting
        info = yf.Ticker(ticker).info
        market_cap = info.get("marketCap") or 0
        fcf = info.get("freeCashflow") or 0
        fcf_yield = round(fcf / market_cap, 4) if market_cap > 0 and fcf else None

        pe = info.get("trailingPE")
        if pe and (pe < 0 or pe > 1000):
            pe = None

        eps_growth = info.get("earningsGrowth")
        if eps_growth and abs(eps_growth) > 5:
            eps_growth = None

        rev_growth = info.get("revenueGrowth")
        if rev_growth and abs(rev_growth) > 5:
            rev_growth = None

        roe = info.get("returnOnEquity")
        if roe and abs(roe) > 5:
            roe = None

        de = info.get("debtToEquity")
        if de:
            de = de / 100  # yfinance returns as percentage
        if de and de > 20:
            de = None

        return ticker, {
            "pe": round(pe, 2) if pe else None,
            "eps_growth": round(eps_growth, 4) if eps_growth else None,
            "rev_growth": round(rev_growth, 4) if rev_growth else None,
            "fcf_yield": fcf_yield,
            "roe": round(roe, 4) if roe else None,
            "debt_equity": round(de, 4) if de else None,
            "market_cap": market_cap,
            "name": info.get("shortName") or info.get("longName") or ticker,
            "sector": info.get("sector") or "Unknown",
        }
    except Exception as e:
        return ticker, {}


def main():
    # Load ticker list
    tickers = None
    if os.path.exists("sp500_meta.json"):
        try:
            with open("sp500_meta.json", encoding="utf-8") as f:
                meta = json.load(f)
            tickers = meta["tickers"]
        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(f"sp500_meta.json unreadable ({e}), falling back to Wikipedia")
    if tickers is None:
        import io
        import pandas as pd
        import requests as _req
        html = _req.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0 SP500-Dashboard/1.0"},
            timeout=15,
        ).text
        table = pd.read_html(io.StringIO(html))[0]
        tickers = table["Symbol"].str.replace(".", "-", regex=False).tolist()

    print(f"Fetching fundamentals for {len(tickers)} stocks (throttled, ~5-10 min)...")

    cache = {}
    completed = 0

    # ThreadPoolExecutor with modest parallelism to avoid rate limits
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_fundamentals, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, data = future.result()
            if data:
                cache[ticker] = data
            completed += 1
            if completed % 50 == 0:
                print(f"  {completed}/{len(tickers)} done...")

    with open("fundamentals_cache.json", "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    print(f"fundamentals_cache.json written — {len(cache)} stocks cached.")


if __name__ == "__main__":
    main()
