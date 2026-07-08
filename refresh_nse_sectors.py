"""
refresh_nse_sectors.py

Builds nse-sectors.json - a symbol -> {name, sector} lookup covering the
full NSE 500 list, not just whichever ~15 names are in today's momentum
screen. The dashboard's Portfolio tab uses this to resolve Sector for any
tracked watchlist/holding symbol, even ones that never show up in the
momentum screener (e.g. TCS, ITC, POWERGRID).

Uses only the Python standard library. Source: the same NSE 500 constituent
CSV that refresh_momentum_picks.py already downloads to build its screening
universe - this script just keeps the full symbol/name/sector table instead
of discarding it after screening.

Usage:
    python refresh_nse_sectors.py
"""

import csv
import json
from io import StringIO
from typing import Any, Dict
from urllib.request import Request, urlopen

NSE_500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
OUTPUT_FILE = "nse-sectors.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 MomentumDashboard/1.0",
    "Accept": "text/csv,application/json,text/plain,*/*",
}


def fetch_text(url: str, timeout: int = 30) -> str:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def build_lookup() -> Dict[str, Dict[str, Any]]:
    content = fetch_text(NSE_500_URL)
    rows = csv.DictReader(StringIO(content))
    lookup: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = (row.get("Symbol") or "").strip().upper()
        if not symbol or row.get("Series") != "EQ" or symbol.startswith("DUMMY"):
            continue
        lookup[symbol] = {
            "name": (row.get("Company Name") or "").replace(" Ltd.", "").replace(" Limited", "").strip(),
            "sector": (row.get("Industry") or "").strip(),
        }
    return lookup


def main() -> None:
    lookup = build_lookup()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(lookup, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {len(lookup)} symbols to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
