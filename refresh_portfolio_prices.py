"""
refresh_portfolio_prices.py

Refreshes portfolio-prices.json with the latest available price for every
symbol currently tracked in portfolio-data.json (watchlist + holdings + open
paper trades). This is what lets the Portfolio tab show a moving "current
price" for stocks that aren't in that day's momentum-picks.json screen -
without it, those prices are frozen at whatever was typed in when added.

Uses only the Python standard library, the same Yahoo Finance chart endpoint
refresh_momentum_picks.py already relies on - no API key required.

Usage:
    python refresh_portfolio_prices.py

Intended to run on a schedule via GitHub Actions
(.github/workflows/refresh-portfolio.yml), but can also be run manually for
a one-off refresh.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

PORTFOLIO_FILE = "portfolio-data.json"
OUTPUT_FILE = "portfolio-prices.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_}&interval={interval}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 MomentumDashboard/1.0",
    "Accept": "application/json,text/plain,*/*",
}


def fetch_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def yahoo_symbol(nse_symbol: str) -> str:
    return quote(f"{nse_symbol}.NS", safe="")


def latest_close(raw: Dict[str, Any]) -> Optional[float]:
    try:
        result = raw["chart"]["result"][0]
        closes = result["indicators"]["quote"][0].get("close", [])
    except (KeyError, IndexError, TypeError):
        return None
    for value in reversed(closes):
        if value is not None:
            return round(float(value), 2)
    return None


def fetch_current_price(symbol: str) -> Optional[float]:
    """Tries today's intraday bars first (freshest during market hours),
    falls back to the most recent daily close (weekends/holidays/pre-open)."""
    symbol_q = yahoo_symbol(symbol)
    try:
        raw = fetch_json(YAHOO_CHART_URL.format(symbol=symbol_q, range_="1d", interval="5m"))
        price = latest_close(raw)
        if price is not None:
            return price
    except Exception:
        pass
    try:
        raw = fetch_json(YAHOO_CHART_URL.format(symbol=symbol_q, range_="5d", interval="1d"))
        return latest_close(raw)
    except Exception:
        return None


def collect_tracked_symbols(portfolio: Dict[str, Any]) -> List[str]:
    symbols = set()
    for item in portfolio.get("watchlist", []):
        if item.get("symbol"):
            symbols.add(item["symbol"].upper())
    for item in portfolio.get("holdings", []):
        if item.get("symbol"):
            symbols.add(item["symbol"].upper())
    for item in portfolio.get("paperTrades", []):
        if item.get("status") == "open" and item.get("symbol"):
            symbols.add(item["symbol"].upper())
    return sorted(symbols)


def main() -> None:
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            portfolio = json.load(f)
    except FileNotFoundError:
        print(f"{PORTFOLIO_FILE} not found - nothing to refresh.")
        return

    symbols = collect_tracked_symbols(portfolio)
    if not symbols:
        print("No tracked symbols in watchlist/holdings/open paper trades - nothing to fetch.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump({"updatedAt": datetime.now(timezone.utc).isoformat(), "prices": {}}, f, indent=2)
            f.write("\n")
        return

    prices: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for symbol in symbols:
        price = fetch_current_price(symbol)
        if price is not None:
            prices[symbol] = {"price": price, "asOf": now_iso}
        else:
            print(f"skip {symbol}: no price data available")

    output = {"updatedAt": now_iso, "prices": prices}
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"wrote {len(prices)}/{len(symbols)} prices to {OUTPUT_FILE} at {now_iso}")


if __name__ == "__main__":
    main()
